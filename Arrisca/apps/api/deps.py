"""Dependências reutilizáveis das rotas FastAPI.

Use com `Depends()`:

    @router.get("/me")
    async def me(user: UserContext = Depends(get_current_user)):
        ...
"""
from __future__ import annotations

from typing import AsyncIterator

import asyncpg
from fastapi import Depends, Header, HTTPException, Request, status

from core.db.pool import get_pool
from core.services.permissions import UserContext
from core.services.supabase_auth import InvalidToken, verify_supabase_jwt
from core.services.user_context import UserNotProvisioned, load_user_context


# =============================================================================
# Database connection (do pool)
# =============================================================================

async def get_db() -> AsyncIterator[asyncpg.Connection]:
    async with get_pool().acquire() as conn:
        yield conn


# =============================================================================
# Usuário autenticado (a partir do JWT do Supabase)
# =============================================================================

async def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    db: asyncpg.Connection = Depends(get_db),
) -> UserContext:
    """Valida o JWT, carrega UserContext, e anexa no request.state."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Token ausente")

    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = verify_supabase_jwt(token)
    except InvalidToken as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=str(e))

    try:
        user_ctx = await load_user_context(db, claims.auth_id)
    except UserNotProvisioned as e:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=f"Usuário sem vínculo a tenant: {e}",
        )

    # Disponibiliza no state para middlewares (ex.: audit logger)
    request.state.user = user_ctx
    return user_ctx


# =============================================================================
# Helpers para restringir endpoints por role
# =============================================================================

def require_role(*allowed: str):
    """Dependência que exige uma das roles primárias passadas."""

    async def checker(user: UserContext = Depends(get_current_user)) -> UserContext:
        if user.primary_role not in allowed:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail=f"Requer uma das roles: {', '.join(allowed)}",
            )
        return user

    return checker


def require_executive_or_above():
    return require_role("super_admin", "executive")


def require_manager_or_above():
    return require_role("super_admin", "executive", "manager")
