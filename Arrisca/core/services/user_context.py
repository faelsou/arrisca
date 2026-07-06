"""Carrega UserContext (papel + áreas) do banco a partir do auth_id do Supabase."""
from __future__ import annotations

from uuid import UUID

import asyncpg

from core.services.permissions import AreaMembership, UserContext


class UserNotProvisioned(Exception):
    """Usuário autenticou no Supabase mas ainda não tem registro em public.users.

    Acontece quando um novo usuário se cadastra e ainda não foi vinculado a
    um tenant. A API deve responder 403 e o admin precisa criar o vínculo.
    """


async def load_user_context(conn: asyncpg.Connection, auth_id: str) -> UserContext:
    """Carrega o contexto completo do usuário a partir do auth_id (sub do JWT)."""
    user_row = await conn.fetchrow(
        """
        SELECT id, tenant_id, primary_role, active
        FROM users
        WHERE auth_id = $1
        """,
        UUID(auth_id),
    )
    if not user_row:
        raise UserNotProvisioned(f"Usuário {auth_id} sem vínculo em public.users")
    if not user_row["active"]:
        raise UserNotProvisioned("Usuário inativo")

    membership_rows = await conn.fetch(
        """
        SELECT area_id, level
        FROM area_memberships
        WHERE user_id = $1 AND revoked_at IS NULL
        """,
        user_row["id"],
    )

    memberships = tuple(
        AreaMembership(area_id=m["area_id"], level=m["level"])
        for m in membership_rows
    )

    return UserContext(
        user_id=user_row["id"],
        tenant_id=user_row["tenant_id"],
        primary_role=user_row["primary_role"],
        area_memberships=memberships,
    )
