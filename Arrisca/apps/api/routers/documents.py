"""Rotas de documentos: upload, listagem, atualização.

POST /documents          → cria registro + dispara ingestão (Celery)
GET  /documents          → lista documentos visíveis ao usuário
PATCH /documents/{id}    → muda sensibilidade ou marca como obsoleto
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from apps.api.deps import get_current_user, get_db
from core.services.audit import log_document_upload, log_event, AuditEvent
from core.services.permissions import (
    Sensitivity, UserContext,
    can_change_document_sensitivity, can_upload_document,
)


router = APIRouter(prefix="/documents", tags=["documents"])


# =============================================================================
# Schemas
# =============================================================================

class CreateDocument(BaseModel):
    """Cria o registro do documento. O upload do arquivo em si vai para S3
    via URL pré-assinada (ou outro fluxo) — aqui só guardamos o source_uri."""
    area_id: UUID
    title: str = Field(min_length=1, max_length=500)
    sensitivity: Literal["public", "internal", "restricted", "confidential"] = "internal"
    source_uri: str = Field(min_length=1)
    content_type: str = Field(default="application/pdf")


class UpdateDocument(BaseModel):
    sensitivity: Literal["public", "internal", "restricted", "confidential"] | None = None
    is_current: bool | None = None
    title: str | None = Field(default=None, max_length=500)


# =============================================================================
# Endpoints
# =============================================================================

@router.post("")
async def create_document(
    body: CreateDocument,
    user: UserContext = Depends(get_current_user),
    db: asyncpg.Connection = Depends(get_db),
) -> dict:
    sensitivity = Sensitivity.from_slug(body.sensitivity)

    if not can_upload_document(user, body.area_id, sensitivity):
        raise HTTPException(403, "Sem permissão para subir documento nessa área/sensibilidade")

    # Verifica que a área existe no tenant
    area = await db.fetchrow(
        "SELECT id, name FROM areas WHERE id = $1 AND tenant_id = $2",
        body.area_id, user.tenant_id,
    )
    if not area:
        raise HTTPException(400, "Área inválida")

    row = await db.fetchrow(
        """
        INSERT INTO documents (
            tenant_id, area_id, title, sensitivity, source_uri,
            content_type, uploaded_by
        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id, ingestion_status, created_at
        """,
        user.tenant_id, body.area_id, body.title, body.sensitivity,
        body.source_uri, body.content_type, user.user_id,
    )

    # Dispara ingestão em background
    from apps.worker.ingestion import ingest_document
    ingest_document.delay(str(row["id"]))

    await log_document_upload(
        db, user.tenant_id, user.user_id, row["id"],
        body.area_id, body.sensitivity, body.title,
    )

    return {
        "id": str(row["id"]),
        "ingestion_status": row["ingestion_status"],
        "created_at": row["created_at"].isoformat(),
    }


@router.get("")
async def list_documents(
    user: UserContext = Depends(get_current_user),
    db: asyncpg.Connection = Depends(get_db),
    area_id: UUID | None = None,
) -> list[dict]:
    """Lista documentos. Aplica o mesmo filtro de permissão que o retrieval."""
    if user.is_super_admin or user.is_executive:
        sql = """
        SELECT d.id, d.title, d.sensitivity, d.area_id, a.slug AS area_slug,
               d.is_current, d.ingestion_status, d.created_at
        FROM documents d JOIN areas a ON a.id = d.area_id
        WHERE d.tenant_id = $1
          AND ($2::uuid IS NULL OR d.area_id = $2)
        ORDER BY d.created_at DESC
        """
        rows = await db.fetch(sql, user.tenant_id, area_id)
    else:
        # Só áreas do usuário, e respeitando sensibilidade máxima por área
        area_ids = [m.area_id for m in user.area_memberships]
        if not area_ids:
            return []
        sql = """
        SELECT d.id, d.title, d.sensitivity, d.area_id, a.slug AS area_slug,
               d.is_current, d.ingestion_status, d.created_at,
               am.level AS user_level
        FROM documents d
        JOIN areas a ON a.id = d.area_id
        JOIN area_memberships am ON am.area_id = d.area_id AND am.user_id = $2
        WHERE d.tenant_id = $1
          AND d.area_id = ANY($3::uuid[])
          AND am.revoked_at IS NULL
          AND ($4::uuid IS NULL OR d.area_id = $4)
          AND (
              d.sensitivity = 'public'
              OR (am.level = 'manager' AND d.sensitivity <> 'confidential')
              OR (am.level = 'employee' AND d.sensitivity IN ('public', 'internal'))
          )
        ORDER BY d.created_at DESC
        """
        rows = await db.fetch(sql, user.tenant_id, user.user_id, area_ids, area_id)

    return [
        {
            "id": str(r["id"]),
            "title": r["title"],
            "sensitivity": r["sensitivity"],
            "area_id": str(r["area_id"]),
            "area_slug": r["area_slug"],
            "is_current": r["is_current"],
            "ingestion_status": r["ingestion_status"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]


@router.patch("/{document_id}")
async def update_document(
    document_id: UUID,
    body: UpdateDocument,
    user: UserContext = Depends(get_current_user),
    db: asyncpg.Connection = Depends(get_db),
) -> dict:
    doc = await db.fetchrow(
        "SELECT id, area_id, sensitivity FROM documents WHERE id = $1 AND tenant_id = $2",
        document_id, user.tenant_id,
    )
    if not doc:
        raise HTTPException(404, "Documento não encontrado")

    # Se está mudando sensibilidade, valida permissão
    if body.sensitivity is not None:
        new_sens = Sensitivity.from_slug(body.sensitivity)
        if not can_change_document_sensitivity(user, doc["area_id"], new_sens):
            raise HTTPException(403, "Sem permissão para essa sensibilidade")

    # Monta UPDATE dinâmico
    updates = []
    params: list = []
    idx = 1
    if body.sensitivity is not None:
        updates.append(f"sensitivity = ${idx}")
        params.append(body.sensitivity)
        idx += 1
    if body.is_current is not None:
        updates.append(f"is_current = ${idx}")
        params.append(body.is_current)
        idx += 1
    if body.title is not None:
        updates.append(f"title = ${idx}")
        params.append(body.title)
        idx += 1

    if not updates:
        raise HTTPException(400, "Nada a atualizar")

    params.append(document_id)
    await db.execute(
        f"UPDATE documents SET {', '.join(updates)} WHERE id = ${idx}",
        *params,
    )

    await log_event(db, AuditEvent(
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        action="doc.update",
        resource_type="document",
        resource_id=document_id,
        details=body.model_dump(exclude_none=True),
    ))

    return {"updated": True}
