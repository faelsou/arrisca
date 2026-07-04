"""Rotas de chat: conversas e mensagens (com streaming SSE).

POST /conversations                      → cria nova conversa
GET  /conversations                      → lista conversas do usuário
GET  /conversations/{id}/messages        → histórico de mensagens
POST /conversations/{id}/messages        → envia mensagem, recebe stream SSE
"""
from __future__ import annotations

import json
from typing import AsyncIterator, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from apps.agents.area_agent import StreamChunk, stream_area_response
from apps.agents.orchestrator import RoutingDecision, route_message
from apps.agents.transport_agent import run_transport_agent
from apps.api.deps import get_current_user, get_db
from core.services.audit import log_chat_query, log_event, AuditEvent
from core.services.permissions import UserContext, can_use_transport_agent
from core.services.retrieval import persist_message_sources


router = APIRouter(prefix="/conversations", tags=["chat"])


# =============================================================================
# Schemas
# =============================================================================

class CreateConversation(BaseModel):
    agent_type: Literal["area", "transport"] = "area"
    title: str | None = Field(default=None, max_length=500)


class SendMessage(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    created_at: str


# =============================================================================
# CRUD básico de conversas
# =============================================================================

@router.post("")
async def create_conversation(
    body: CreateConversation,
    user: UserContext = Depends(get_current_user),
    db: asyncpg.Connection = Depends(get_db),
) -> dict:
    # Se for transporte, verifica permissão
    if body.agent_type == "transport":
        transport_area = await db.fetchrow(
            "SELECT id FROM areas WHERE tenant_id = $1 AND is_transport = TRUE",
            user.tenant_id,
        )
        if not transport_area:
            raise HTTPException(400, "Tenant não tem área de transporte configurada")
        if not can_use_transport_agent(user, transport_area["id"]):
            raise HTTPException(403, "Usuário sem acesso ao agente de transporte")

    row = await db.fetchrow(
        """
        INSERT INTO conversations (tenant_id, user_id, agent_type, title)
        VALUES ($1, $2, $3, $4)
        RETURNING id, agent_type, title, created_at
        """,
        user.tenant_id, user.user_id, body.agent_type, body.title,
    )
    return {
        "id": str(row["id"]),
        "agent_type": row["agent_type"],
        "title": row["title"],
        "created_at": row["created_at"].isoformat(),
    }


@router.get("")
async def list_conversations(
    user: UserContext = Depends(get_current_user),
    db: asyncpg.Connection = Depends(get_db),
    limit: int = 50,
) -> list[dict]:
    rows = await db.fetch(
        """
        SELECT id, agent_type, title, created_at, updated_at
        FROM conversations
        WHERE user_id = $1
        ORDER BY updated_at DESC
        LIMIT $2
        """,
        user.user_id, limit,
    )
    return [
        {
            "id": str(r["id"]),
            "agent_type": r["agent_type"],
            "title": r["title"],
            "created_at": r["created_at"].isoformat(),
            "updated_at": r["updated_at"].isoformat(),
        }
        for r in rows
    ]


@router.get("/{conversation_id}/messages")
async def list_messages(
    conversation_id: UUID,
    user: UserContext = Depends(get_current_user),
    db: asyncpg.Connection = Depends(get_db),
) -> list[MessageOut]:
    # Verifica que a conversa pertence ao usuário
    conv = await db.fetchrow(
        "SELECT id FROM conversations WHERE id = $1 AND user_id = $2",
        conversation_id, user.user_id,
    )
    if not conv:
        raise HTTPException(404, "Conversa não encontrada")

    rows = await db.fetch(
        """
        SELECT id, role, content, created_at
        FROM messages
        WHERE conversation_id = $1
        ORDER BY created_at
        """,
        conversation_id,
    )
    return [
        MessageOut(
            id=str(r["id"]),
            role=r["role"],
            content=r["content"],
            created_at=r["created_at"].isoformat(),
        )
        for r in rows
    ]


# =============================================================================
# Envio de mensagem (streaming SSE)
# =============================================================================

@router.post("/{conversation_id}/messages")
async def send_message(
    conversation_id: UUID,
    body: SendMessage,
    request: Request,
    user: UserContext = Depends(get_current_user),
    db: asyncpg.Connection = Depends(get_db),
):
    """Envia mensagem do usuário e retorna a resposta do agente via SSE.

    Eventos SSE enviados:
        event: sources    — chunks recuperados (antes da resposta começar)
        event: text       — tokens da resposta sendo gerados
        event: routing    — decisão do orquestrador (para debug/transparência)
        event: done       — fim, com ID da mensagem salva
        event: error      — erro durante a geração
    """
    # 1. Valida a conversa
    conv = await db.fetchrow(
        """
        SELECT id, agent_type, tenant_id
        FROM conversations
        WHERE id = $1 AND user_id = $2
        """,
        conversation_id, user.user_id,
    )
    if not conv:
        raise HTTPException(404, "Conversa não encontrada")

    # 2. Salva a mensagem do usuário ANTES do processamento (auditável mesmo se falhar)
    user_msg_row = await db.fetchrow(
        """
        INSERT INTO messages (conversation_id, role, content)
        VALUES ($1, 'user', $2)
        RETURNING id
        """,
        conversation_id, body.content,
    )

    # 3. Decide o agente
    decision = await route_message(
        message=body.content,
        user=user,
        conversation_agent_type=conv["agent_type"],
        db=db,
    )

    # 4. Carrega histórico recente (últimas 10 mensagens) para contexto
    history_rows = await db.fetch(
        """
        SELECT role, content FROM messages
        WHERE conversation_id = $1 AND id != $2
        ORDER BY created_at DESC LIMIT 10
        """,
        conversation_id, user_msg_row["id"],
    )
    history = [
        {"role": r["role"], "content": r["content"]}
        for r in reversed(history_rows)
    ]

    # 5. Gera resposta em streaming
    ip = request.client.host if request.client else None

    async def event_generator() -> AsyncIterator[dict]:
        yield {
            "event": "routing",
            "data": json.dumps({
                "agent": decision.agent_type,
                "area_slug": decision.area_slug,
                "reasoning": decision.reasoning,
            }),
        }

        if decision.agent_type == "transport":
            async for ev in _run_transport(
                db=db, user=user, conversation_id=conversation_id,
                user_message=body.content, history=history, ip=ip,
            ):
                yield ev
        else:
            async for ev in _run_area(
                db=db, user=user, conversation_id=conversation_id,
                user_message=body.content, history=history, ip=ip,
                decision=decision,
            ):
                yield ev

    return EventSourceResponse(event_generator())


# =============================================================================
# Implementação por agente
# =============================================================================

async def _run_area(
    *, db, user, conversation_id, user_message, history, ip, decision: RoutingDecision,
) -> AsyncIterator[dict]:
    """Roda o agente de área com streaming SSE."""
    full_text_parts: list[str] = []
    chunks_used: list[str] = []
    usage = {}

    async for ch in stream_area_response(
        user_message=user_message,
        user=user,
        db=db,
        area_id_hint=decision.area_id_hint,
        area_name=decision.area_slug,
        conversation_history=history,
    ):
        if ch.type == "sources":
            yield {"event": "sources", "data": json.dumps(ch.metadata)}
        elif ch.type == "text":
            full_text_parts.append(ch.content)
            yield {"event": "text", "data": json.dumps({"content": ch.content})}
        elif ch.type == "done":
            usage = (ch.metadata or {}).get("usage", {})
            chunks_used = (ch.metadata or {}).get("chunks_used", [])
        elif ch.type == "error":
            yield {"event": "error", "data": json.dumps({"message": ch.content})}
            return

    full_text = "".join(full_text_parts)

    # Persiste a resposta + auditoria
    msg_id = await _save_assistant_message(
        db, conversation_id, full_text, usage, chunks_used, decision,
    )

    await log_chat_query(
        db, user.tenant_id, user.user_id, conversation_id,
        user_message, [UUID(c) for c in chunks_used], ip,
    )

    yield {"event": "done", "data": json.dumps({"message_id": str(msg_id)})}


async def _run_transport(
    *, db, user, conversation_id, user_message, history, ip,
) -> AsyncIterator[dict]:
    """Roda o agente de transporte. Não usa streaming (tool loop seria complexo de stremear)."""
    yield {"event": "text", "data": json.dumps({"content": "Calculando rota e custos...\n\n"})}

    result = await run_transport_agent(
        user_message=user_message,
        db=db,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        conversation_id=conversation_id,
        conversation_history=history,
    )

    # Emite a resposta inteira como um único bloco de texto
    yield {"event": "text", "data": json.dumps({"content": result.text})}

    # Salva mensagem do assistente
    msg_row = await db.fetchrow(
        """
        INSERT INTO messages (conversation_id, role, content, metadata)
        VALUES ($1, 'assistant', $2, $3::jsonb)
        RETURNING id
        """,
        conversation_id, result.text,
        json.dumps({
            "tool_calls": result.tool_calls,
            "total_tokens": result.total_tokens,
            "iterations": result.iterations,
            "agent": "transport",
        }),
    )

    await log_event(db, AuditEvent(
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        action="transport.quote",
        resource_type="conversation",
        resource_id=conversation_id,
        ip_address=ip,
        details={"iterations": result.iterations, "tokens": result.total_tokens},
    ))

    yield {"event": "done", "data": json.dumps({"message_id": str(msg_row["id"])})}


# =============================================================================
# Persistência da resposta do assistente (modo área)
# =============================================================================

async def _save_assistant_message(
    db, conversation_id, content, usage, chunks_used: list[str], decision,
) -> UUID:
    msg_row = await db.fetchrow(
        """
        INSERT INTO messages (conversation_id, role, content, metadata)
        VALUES ($1, 'assistant', $2, $3::jsonb)
        RETURNING id
        """,
        conversation_id, content,
        json.dumps({
            "usage": usage,
            "agent": "area",
            "area_slug": decision.area_slug,
            "routing_reasoning": decision.reasoning,
        }),
    )

    # Persiste citações (message_sources)
    if chunks_used:
        await db.executemany(
            """
            INSERT INTO message_sources (message_id, chunk_id, rank)
            VALUES ($1, $2, $3)
            """,
            [(msg_row["id"], UUID(cid), i) for i, cid in enumerate(chunks_used)],
        )

    return msg_row["id"]
