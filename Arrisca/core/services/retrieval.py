"""retrieval — RAG retrieval com RBAC enforced no SQL.

A função `retrieve_chunks` é o ponto único onde:
    1. fazemos vector search (HNSW via pgvector `<=>`)
    2. aplicamos o predicado de visibilidade RBAC

As duas coisas vão NO MESMO query, para que o ORDER BY + LIMIT operem
apenas sobre chunks que o usuário tem permissão de ver. Não há vazamento
possível: chunks proibidos nunca chegam ao LLM porque nunca saem do
banco.

SUPOSIÇÕES DE SCHEMA (ajustar se divergir do 001_initial_schema.sql):
    * document_chunks:
        id, document_id, tenant_id, area_id, sensitivity, is_current,
        content, embedding (vector), chunk_index
    * documents:
        id, title
    * message_sources (tabela de junção para auditar quais chunks
      foram citados em cada mensagem do assistente):
        message_id, chunk_id, similarity, position
      Se sua tabela tiver outro nome / outras colunas, ajustar a query
      de `persist_message_sources` abaixo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence
from uuid import UUID

import asyncpg

from core.services.permissions import (
    Sensitivity,
    UserContext,
    build_chunk_visibility_sql,
)


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """Chunk recuperado pronto para ser entregue ao LLM."""
    chunk_id: UUID
    document_id: UUID
    document_title: str
    area_id: UUID
    sensitivity: Sensitivity
    content: str
    chunk_index: int
    similarity: float  # 1.0 = idêntico, ~0 = ortogonal (cosine similarity)


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _embedding_to_pgvector(embedding: Sequence[float]) -> str:
    """Converte lista de floats para o formato textual aceito pelo
    cast `::vector` do pgvector: '[0.1, 0.2, ...]'.

    Usar este formato (em vez de passar a lista direto) evita ambiguidade
    de tipo no asyncpg quando o driver ainda não tem codec registrado.
    """
    return "[" + ",".join(f"{x:.7f}" for x in embedding) + "]"


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

async def retrieve_chunks(
    pool: asyncpg.Pool,
    *,
    ctx: UserContext,
    query_embedding: Sequence[float],
    area_id: Optional[UUID] = None,
    limit: int = 8,
    similarity_threshold: float = 0.0,
) -> list[RetrievedChunk]:
    """Vector search + RBAC em um único query.

    Args:
        pool: pool asyncpg.
        ctx: contexto do usuário (define o universo de chunks visíveis).
        query_embedding: vetor da consulta (mesma dimensão dos embeddings
            armazenados, ex: 1536 para text-embedding-3-small).
        area_id: se fornecido, restringe ainda mais a busca a essa única
            área (mesmo que o usuário tenha acesso a várias). Útil para
            o agente de área, que deve focar em UMA área de cada vez.
        limit: número máximo de chunks a retornar.
        similarity_threshold: descarta chunks com similarity abaixo
            disto. 0.0 = sem filtro. Recomenda-se 0.2-0.3 em produção.

    Returns:
        Lista de RetrievedChunk ordenada por similarity decrescente.
    """
    if limit <= 0:
        return []

    embedding_literal = _embedding_to_pgvector(query_embedding)

    # Placeholders: $1 = embedding, $2 = limit, $3 = similarity_threshold,
    # $4 = area_id (nullable). Visibility predicate começa em $5.
    viz = build_chunk_visibility_sql(ctx, start_placeholder=5)

    sql = f"""
        SELECT
            dc.id            AS chunk_id,
            dc.document_id   AS document_id,
            d.title          AS document_title,
            dc.area_id       AS area_id,
            dc.sensitivity   AS sensitivity,
            dc.content       AS content,
            dc.chunk_index   AS chunk_index,
            1 - (dc.embedding <=> $1::vector) AS similarity
        FROM document_chunks dc
        JOIN documents d ON d.id = dc.document_id
        WHERE {viz.where_sql}
          AND ($4::uuid IS NULL OR dc.area_id = $4::uuid)
          AND 1 - (dc.embedding <=> $1::vector) >= $3
        ORDER BY dc.embedding <=> $1::vector
        LIMIT $2
    """

    params: list[object] = [
        embedding_literal,
        limit,
        similarity_threshold,
        area_id,
        *viz.params,
    ]

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    return [
        RetrievedChunk(
            chunk_id=row["chunk_id"],
            document_id=row["document_id"],
            document_title=row["document_title"],
            area_id=row["area_id"],
            sensitivity=Sensitivity(row["sensitivity"]),
            content=row["content"],
            chunk_index=row["chunk_index"],
            similarity=float(row["similarity"]),
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Persistência das fontes citadas
# ---------------------------------------------------------------------------

async def persist_message_sources(
    pool: asyncpg.Pool,
    *,
    message_id: UUID,
    chunks: list[RetrievedChunk],
) -> None:
    """Registra quais chunks foram entregues ao LLM como contexto para
    gerar uma dada mensagem do assistente.

    Importante para:
        * UI: exibir fontes/citações abaixo da resposta.
        * Auditoria: depois conseguir provar que um chunk X foi usado
          em uma resposta Y, dado um usuário Z.

    Idempotente: chamar de novo com o mesmo message_id sobrescreve as
    fontes anteriores (via DELETE + INSERT dentro de uma transação).
    """
    if not chunks:
        return

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM message_sources WHERE message_id = $1",
                message_id,
            )
            await conn.executemany(
                """
                INSERT INTO message_sources
                    (message_id, chunk_id, similarity, position)
                VALUES ($1, $2, $3, $4)
                """,
                [
                    (message_id, c.chunk_id, c.similarity, position)
                    for position, c in enumerate(chunks)
                ],
            )


__all__ = [
    "RetrievedChunk",
    "retrieve_chunks",
    "persist_message_sources",
]
