"""Agente genérico de área: faz RAG com permissão e responde via Claude streaming.

Por que streaming: a resposta de uma pergunta vai sendo enviada token a token
pro frontend (SSE), o que melhora muito a percepção de latência. O usuário
começa a ler antes de o LLM terminar.

Por que parametrizado em vez de um agente por área: economiza código.
A diferença entre "agente de RH" e "agente de Marketing" é só o prompt
e os documentos que ele tem acesso. Ambos são providenciados em runtime
pela função de retrieval, que já aplica as permissões.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator
from uuid import UUID

import asyncpg
from anthropic import AsyncAnthropic

from core.services.permissions import UserContext
from core.services.retrieval import RetrievedChunk, retrieve_chunks


MODEL = "claude-sonnet-4-6"
EMBED_MODEL = "text-embedding-3-small"


SYSTEM_PROMPT_TEMPLATE = """Você é um assistente da gráfica Arrisca, especializado em ajudar
funcionários da área de {area_name}.

Use APENAS as informações nos documentos fornecidos abaixo para responder.
Se a resposta não estiver nos documentos, diga claramente "Não encontrei
essa informação nos documentos disponíveis" e sugira a quem perguntar.

Quando citar uma informação, indique a fonte usando o formato [doc N] ao
final da frase, onde N é o número do documento na lista.

Responda em português brasileiro, de forma direta e profissional.

DOCUMENTOS DISPONÍVEIS:
{context}
"""


SYSTEM_PROMPT_NO_CONTEXT = """Você é um assistente da gráfica Arrisca.
A pergunta do usuário não corresponde a nenhum documento disponível
para o nível de acesso dele.

Responda educadamente que não tem informações específicas sobre o tópico
e sugira que o usuário fale com seu gerente ou com a área responsável.

Responda em português brasileiro, de forma direta e profissional.
"""


@dataclass
class StreamChunk:
    """Pedaço da resposta sendo enviado via SSE."""
    type: str         # 'text' | 'sources' | 'done' | 'error'
    content: str = ""
    metadata: dict | None = None


# =============================================================================
# Geração do embedding da pergunta
# =============================================================================

async def embed_query(text: str) -> list[float]:
    """Gera embedding para a pergunta do usuário (mesmo modelo da ingestão)."""
    from openai import AsyncOpenAI
    client = AsyncOpenAI()
    resp = await client.embeddings.create(model=EMBED_MODEL, input=text)
    return resp.data[0].embedding


# =============================================================================
# Stream de resposta do agente
# =============================================================================

async def stream_area_response(
    user_message: str,
    user: UserContext,
    db: asyncpg.Connection,
    area_id_hint: UUID | None,
    area_name: str | None,
    conversation_history: list[dict] | None = None,
) -> AsyncIterator[StreamChunk]:
    """Gera a resposta do agente, em streaming, no formato StreamChunk.

    Sequência de eventos:
        1. 'sources' — lista dos chunks recuperados (frontend pode mostrar antes da resposta)
        2. múltiplos 'text' — texto da resposta sendo gerado
        3. 'done' — fim, com metadata (tokens, ids, etc.)
        4. 'error' — se algo der errado

    A função que monta a SSE response (no router) é quem decide como
    serializar cada StreamChunk pro wire.
    """
    try:
        # 1. Embedding da pergunta
        query_embedding = await embed_query(user_message)

        # 2. Retrieval com filtro de permissão (segurança crítica está aqui)
        chunks = await retrieve_chunks(
            conn=db,
            user=user,
            query_embedding=query_embedding,
            area_id_hint=area_id_hint,
            top_k=8,
            min_similarity=0.4,
        )

        # 3. Emite os fontes ANTES da resposta (UX: usuário sabe sobre o que vão se basear)
        yield StreamChunk(
            type="sources",
            metadata={
                "chunks": [
                    {
                        "chunk_id": str(c.chunk_id),
                        "document_id": str(c.document_id),
                        "document_title": c.document_title,
                        "area_slug": c.area_slug,
                        "similarity": round(c.similarity, 3),
                        "rank": c.rank,
                    }
                    for c in chunks
                ],
            },
        )

        # 4. Monta o prompt do sistema
        if not chunks:
            system_prompt = SYSTEM_PROMPT_NO_CONTEXT
        else:
            context_blocks = _format_chunks_as_context(chunks)
            system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
                area_name=area_name or "geral",
                context=context_blocks,
            )

        # 5. Stream da resposta do Claude
        messages = list(conversation_history or [])
        messages.append({"role": "user", "content": user_message})

        client = AsyncAnthropic()
        full_text = ""
        usage = {"input_tokens": 0, "output_tokens": 0}

        async with client.messages.stream(
            model=MODEL,
            max_tokens=2048,
            system=system_prompt,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                full_text += text
                yield StreamChunk(type="text", content=text)

            final_message = await stream.get_final_message()
            usage["input_tokens"] = final_message.usage.input_tokens
            usage["output_tokens"] = final_message.usage.output_tokens

        # 6. Sinaliza fim, com metadados para o router persistir
        yield StreamChunk(
            type="done",
            metadata={
                "full_text": full_text,
                "usage": usage,
                "chunks_used": [str(c.chunk_id) for c in chunks],
            },
        )

    except Exception as e:
        yield StreamChunk(type="error", content=str(e))


def _format_chunks_as_context(chunks: list[RetrievedChunk]) -> str:
    """Formata os chunks recuperados como contexto para o LLM."""
    blocks = []
    for i, c in enumerate(chunks, start=1):
        blocks.append(
            f"[doc {i}] {c.document_title} (área: {c.area_slug})\n"
            f"{c.content}\n"
        )
    return "\n---\n".join(blocks)
