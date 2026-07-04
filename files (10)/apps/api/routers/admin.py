"""Rotas administrativas: gestão de usuários, permissões e audit log."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from apps.api.deps import get_current_user, get_db, require_executive_or_above
from core.services.audit import log_event, query_audit_log, AuditEvent
from core.services.permissions import (
    UserContext, can_change_primary_role, can_grant_membership, can_view_audit_log,
)


router = APIRouter(prefix="/admin", tags=["admin"])


# =============================================================================
# Usuários
# =============================================================================

class CreateUser(BaseModel):
    """Cria usuário em public.users referenciando um auth_id já existente
    em auth.users do Supabase (criar lá primeiro via signUp ou painel)."""
    auth_id: UUID
    email: str
    name: str
    primary_role: Literal["executive", "manager", "employee"] = "employee"


@router.get("/users")
async def list_users(
    user: UserContext = Depends(require_executive_or_above()),
    db: asyncpg.Connection = Depends(get_db),
) -> list[dict]:
    rows = await db.fetch(
        """
        SELECT u.id, u.email, u.name, u.primary_role, u.active, u.created_at,
            COALESCE(
                jsonb_agg(
                    jsonb_build_object('area_id', am.area_id, 'level', am.level)
                ) FILTER (WHERE am.id IS NOT NULL AND am.revoked_at IS NULL),
                '[]'::jsonb
            ) AS memberships
        FROM users u
        LEFT JOIN area_memberships am ON am.user_id = u.id
        WHERE u.tenant_id = $1
        GROUP BY u.id
        ORDER BY u.created_at DESC
        """,
        user.tenant_id,
    )
    return [
        {
            "id": str(r["id"]),
            "email": r["email"],
            "name": r["name"],
            "primary_role": r["primary_role"],
            "active": r["active"],
            "memberships": r["memberships"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]


@router.post("/users")
async def create_user(
    body: CreateUser,
    actor: UserContext = Depends(require_executive_or_above()),
    db: asyncpg.Connection = Depends(get_db),
) -> dict:
    # Super admin / executive sempre pode criar employees/managers
    if not can_change_primary_role(actor, body.primary_role):
        raise HTTPException(403, "Sem permissão para criar usuário com esse papel")

    row = await db.fetchrow(
        """
        INSERT INTO users (auth_id, tenant_id, email, name, primary_role)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id, created_at
        """,
        body.auth_id, actor.tenant_id, body.email, body.name, body.primary_role,
    )

    await log_event(db, AuditEvent(
        tenant_id=actor.tenant_id, user_id=actor.user_id,
        action="user.create", resource_type="user", resource_id=row["id"],
        details={"email": body.email, "role": body.primary_role},
    ))

    return {"id": str(row["id"]), "created_at": row["created_at"].isoformat()}


class UpdateUserRole(BaseModel):
    primary_role: Literal["executive", "manager", "employee"]


@router.patch("/users/{user_id}/role")
async def update_user_role(
    user_id: UUID,
    body: UpdateUserRole,
    actor: UserContext = Depends(get_current_user),
    db: asyncpg.Connection = Depends(get_db),
) -> dict:
    if not can_change_primary_role(actor, body.primary_role):
        raise HTTPException(403, "Sem permissão para mudar para esse papel")

    target = await db.fetchrow(
        "SELECT id, tenant_id, primary_role FROM users WHERE id = $1",
        user_id,
    )
    if not target or target["tenant_id"] != actor.tenant_id:
        raise HTTPException(404, "Usuário não encontrado")

    await db.execute(
        "UPDATE users SET primary_role = $1 WHERE id = $2",
        body.primary_role, user_id,
    )

    await log_event(db, AuditEvent(
        tenant_id=actor.tenant_id, user_id=actor.user_id,
        action="user.role_change", resource_type="user", resource_id=user_id,
        details={
            "from": target["primary_role"],
            "to": body.primary_role,
        },
    ))
    return {"updated": True}


# =============================================================================
# Memberships (vínculo usuário ↔ área com nível)
# =============================================================================

class GrantMembership(BaseModel):
    area_id: UUID
    level: Literal["employee", "manager"] = "employee"


@router.post("/users/{user_id}/memberships")
async def grant_membership(
    user_id: UUID,
    body: GrantMembership,
    actor: UserContext = Depends(get_current_user),
    db: asyncpg.Connection = Depends(get_db),
) -> dict:
    if not can_grant_membership(actor, body.area_id, body.level):
        raise HTTPException(403, "Sem permissão para conceder essa membership")

    # Verifica que o user-alvo está no mesmo tenant
    target = await db.fetchrow(
        "SELECT id FROM users WHERE id = $1 AND tenant_id = $2",
        user_id, actor.tenant_id,
    )
    if not target:
        raise HTTPException(404, "Usuário não encontrado")

    # Upsert (se já existe e está revogado, reativa; se está ativo, atualiza nível)
    row = await db.fetchrow(
        """
        INSERT INTO area_memberships (user_id, area_id, level, granted_by)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (user_id, area_id) DO UPDATE
        SET level = EXCLUDED.level,
            granted_by = EXCLUDED.granted_by,
            granted_at = NOW(),
            revoked_at = NULL
        RETURNING id
        """,
        user_id, body.area_id, body.level, actor.user_id,
    )

    await log_event(db, AuditEvent(
        tenant_id=actor.tenant_id, user_id=actor.user_id,
        action="perm.grant", resource_type="area_membership", resource_id=row["id"],
        details={"target_user": str(user_id), "area_id": str(body.area_id), "level": body.level},
    ))

    return {"id": str(row["id"])}


@router.delete("/users/{user_id}/memberships/{area_id}")
async def revoke_membership(
    user_id: UUID,
    area_id: UUID,
    actor: UserContext = Depends(get_current_user),
    db: asyncpg.Connection = Depends(get_db),
) -> dict:
    # Para revogar, usa a mesma checagem de granting (quem pode dar, pode tirar)
    if not can_grant_membership(actor, area_id, "employee"):
        raise HTTPException(403, "Sem permissão para revogar membership")

    res = await db.execute(
        """
        UPDATE area_memberships
        SET revoked_at = NOW()
        WHERE user_id = $1 AND area_id = $2 AND revoked_at IS NULL
        """,
        user_id, area_id,
    )
    if res == "UPDATE 0":
        raise HTTPException(404, "Membership não encontrada ou já revogada")

    await log_event(db, AuditEvent(
        tenant_id=actor.tenant_id, user_id=actor.user_id,
        action="perm.revoke", resource_type="area_membership",
        details={"target_user": str(user_id), "area_id": str(area_id)},
    ))
    return {"revoked": True}


# =============================================================================
# Audit log
# =============================================================================

@router.get("/audit")
async def get_audit_log(
    actor: UserContext = Depends(get_current_user),
    db: asyncpg.Connection = Depends(get_db),
    user_id: UUID | None = None,
    action: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    if not can_view_audit_log(actor):
        raise HTTPException(403, "Sem permissão para visualizar audit log")

    events = await query_audit_log(
        conn=db, tenant_id=actor.tenant_id,
        user_id=user_id, action=action, limit=limit, offset=offset,
    )
    return [
        {
            **{k: v for k, v in e.items() if k not in ("created_at", "id")},
            "id": e["id"],
            "tenant_id": str(e["tenant_id"]),
            "user_id": str(e["user_id"]) if e["user_id"] else None,
            "resource_id": str(e["resource_id"]) if e["resource_id"] else None,
            "ip_address": str(e["ip_address"]) if e["ip_address"] else None,
            "created_at": e["created_at"].isoformat(),
        }
        for e in events
    ]
