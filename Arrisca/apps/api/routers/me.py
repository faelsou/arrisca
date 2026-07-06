"""Rotas relacionadas ao usuário autenticado (não-admin)."""
from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import APIRouter, Depends

from apps.api.deps import get_current_user, get_db
from core.services.permissions import UserContext


router = APIRouter(prefix="/me", tags=["me"])


@router.get("")
async def get_me(
    user: UserContext = Depends(get_current_user),
    db: asyncpg.Connection = Depends(get_db),
) -> dict[str, Any]:
    """Dados do usuário atual + áreas que ele pode acessar."""
    user_row = await db.fetchrow(
        "SELECT email, name, primary_role FROM users WHERE id = $1",
        user.user_id,
    )

    # Detalha as áreas (slug, nome, nível do usuário nela)
    area_rows = await db.fetch(
        """
        SELECT a.id, a.slug, a.name, a.is_transport, am.level
        FROM areas a
        JOIN area_memberships am ON am.area_id = a.id
        WHERE am.user_id = $1 AND am.revoked_at IS NULL
        ORDER BY a.slug
        """,
        user.user_id,
    )

    return {
        "id": str(user.user_id),
        "tenant_id": str(user.tenant_id),
        "email": user_row["email"],
        "name": user_row["name"],
        "primary_role": user.primary_role,
        "areas": [
            {
                "id": str(a["id"]),
                "slug": a["slug"],
                "name": a["name"],
                "is_transport": a["is_transport"],
                "level": a["level"],
            }
            for a in area_rows
        ],
    }
