"""Pool de conexões asyncpg compartilhado para toda a aplicação.

Uso típico em rota FastAPI:

    async def endpoint(db: asyncpg.Connection = Depends(get_db)):
        rows = await db.fetch("SELECT ...")
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg


_pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    global _pool
    if _pool is not None:
        return
    _pool = await asyncpg.create_pool(
        dsn=os.getenv("DATABASE_URL"),
        min_size=2,
        max_size=20,
        # Registra o tipo vector do pgvector
        init=_setup_connection,
    )


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def _setup_connection(conn: asyncpg.Connection) -> None:
    """Configurações por conexão (codec do pgvector, search_path, etc.)."""
    # pgvector: cast bidirecional Python list <-> vector
    await conn.set_type_codec(
        "vector",
        encoder=lambda v: "[" + ",".join(f"{x:.6f}" for x in v) + "]",
        decoder=lambda s: [float(x) for x in s.strip("[]").split(",")] if s else [],
        format="text",
    )


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Pool não inicializado. Chame init_pool() no startup.")
    return _pool


@asynccontextmanager
async def acquire() -> AsyncIterator[asyncpg.Connection]:
    """Adquire uma conexão do pool. Uso fora do FastAPI (workers, scripts)."""
    async with get_pool().acquire() as conn:
        yield conn
