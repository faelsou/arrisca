"""Serviço de auditoria.

Registra TODA ação sensível no audit_log. A tabela tem trigger que rejeita
UPDATE e DELETE — é append-only. Em produção, considerar particionamento
por mês (`CREATE TABLE audit_log_2025_01 PARTITION OF audit_log ...`).

Ações registradas (não exaustivo):
    chat.query          → usuário fez uma pergunta no chat
    chat.response       → assistente respondeu (com chunks usados)
    doc.upload          → upload de documento
    doc.update          → mudança de sensibilidade ou área
    doc.delete          → exclusão (ou marcação obsoleta)
    perm.grant          → concessão de membership
    perm.revoke         → revogação de membership
    user.role_change    → mudança de role primária
    transport.quote     → orçamento de transporte gerado
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg


@dataclass(frozen=True)
class AuditEvent:
    tenant_id: UUID
    user_id: UUID | None
    action: str
    resource_type: str | None = None
    resource_id: UUID | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    details: dict[str, Any] | None = None


async def log_event(conn: asyncpg.Connection, event: AuditEvent) -> int:
    """Insere um evento no audit_log. Retorna o ID inserido."""
    row = await conn.fetchrow(
        """
        INSERT INTO audit_log (
            tenant_id, user_id, action, resource_type, resource_id,
            ip_address, user_agent, details
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
        RETURNING id
        """,
        event.tenant_id,
        event.user_id,
        event.action,
        event.resource_type,
        event.resource_id,
        event.ip_address,
        event.user_agent,
        json.dumps(event.details or {}, default=str),
    )
    return row["id"]


# =============================================================================
# Helpers para os eventos mais comuns
# =============================================================================

async def log_chat_query(
    conn: asyncpg.Connection,
    tenant_id: UUID,
    user_id: UUID,
    conversation_id: UUID,
    query_text: str,
    retrieved_chunk_ids: list[UUID],
    ip_address: str | None = None,
) -> None:
    await log_event(conn, AuditEvent(
        tenant_id=tenant_id,
        user_id=user_id,
        action="chat.query",
        resource_type="conversation",
        resource_id=conversation_id,
        ip_address=ip_address,
        details={
            "query": query_text[:500],   # truncar pra não inflar a tabela
            "retrieved_chunks": [str(c) for c in retrieved_chunk_ids],
        },
    ))


async def log_permission_grant(
    conn: asyncpg.Connection,
    tenant_id: UUID,
    actor_id: UUID,
    target_user_id: UUID,
    area_id: UUID,
    level: str,
) -> None:
    await log_event(conn, AuditEvent(
        tenant_id=tenant_id,
        user_id=actor_id,
        action="perm.grant",
        resource_type="area_membership",
        resource_id=target_user_id,
        details={
            "target_user_id": str(target_user_id),
            "area_id": str(area_id),
            "level": level,
        },
    ))


async def log_document_upload(
    conn: asyncpg.Connection,
    tenant_id: UUID,
    user_id: UUID,
    document_id: UUID,
    area_id: UUID,
    sensitivity: str,
    title: str,
) -> None:
    await log_event(conn, AuditEvent(
        tenant_id=tenant_id,
        user_id=user_id,
        action="doc.upload",
        resource_type="document",
        resource_id=document_id,
        details={
            "area_id": str(area_id),
            "sensitivity": sensitivity,
            "title": title,
        },
    ))


# =============================================================================
# Query de auditoria (para o endpoint de admin)
# =============================================================================

async def query_audit_log(
    conn: asyncpg.Connection,
    tenant_id: UUID,
    user_id: UUID | None = None,
    action: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Lista eventos de auditoria do tenant, mais recentes primeiro."""
    conditions = ["tenant_id = $1"]
    params: list = [tenant_id]
    idx = 2

    if user_id is not None:
        conditions.append(f"user_id = ${idx}")
        params.append(user_id)
        idx += 1

    if action is not None:
        conditions.append(f"action = ${idx}")
        params.append(action)
        idx += 1

    params.extend([limit, offset])
    where = " AND ".join(conditions)

    rows = await conn.fetch(
        f"""
        SELECT id, tenant_id, user_id, action, resource_type, resource_id,
               ip_address, details, created_at
        FROM audit_log
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT ${idx} OFFSET ${idx + 1}
        """,
        *params,
    )
    return [dict(r) for r in rows]
