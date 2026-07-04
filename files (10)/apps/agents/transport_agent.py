"""transport_agent — Agente especializado em análise de custo-benefício de
entregas/transporte (STUB).

Arquitetura prevista (a implementar):
    * Claude (Sonnet) num loop de tool use.
    * Todas as contas numéricas (rotas, combustível, pedágios, seguros,
      mão-de-obra, hospedagem, cotações com transportadoras) ficam em
      funções Python DETERMINÍSTICAS que chamam APIs externas.
    * O LLM apenas: entende o pedido, escolhe quais tools chamar,
      interpreta resultados e redige a resposta em linguagem natural.
    * O LLM NUNCA inventa números — todo valor citado veio de uma tool.

Este arquivo é apenas um stub para permitir que `conversations.py`
importe `run_transport_agent` sem quebrar o boot da API. A implementação
real chega depois.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator
from uuid import UUID

from core.services.permissions import UserContext


@dataclass(frozen=True, slots=True)
class TransportStreamChunk:
    """Token/evento emitido pelo agente de transporte durante o streaming.

    Mesma forma do `StreamChunk` do area_agent (kind + payload), só com
    eventos adicionais para tool calls (que o area_agent não precisa).
    """
    kind: str   # 'text' | 'tool_call' | 'tool_result' | 'done' | 'error'
    payload: dict[str, Any]


async def run_transport_agent(
    *,
    user_message: str,
    user: UserContext,
    conversation_id: UUID,
    db: Any = None,
    **_extra: Any,
) -> AsyncIterator[TransportStreamChunk]:
    """STUB do agente de transporte.

    Mantém uma assinatura flexível (kwargs) para casar com qualquer
    forma que `conversations.py` chame esta função. Quando for
    implementar de verdade, ajustar a assinatura para o que faz sentido.

    Por enquanto emite um único chunk de erro e encerra, em vez de
    levantar exceção — assim o endpoint /conversations não quebra
    com 500 quando alguém pedir o agente de transporte; ele só
    responde "ainda não implementado".
    """
    _ = (user_message, user, conversation_id, db, _extra)
    yield TransportStreamChunk(
        kind="error",
        payload={
            "message": (
                "O agente de Transporte ainda não está implementado. "
                "Em breve será capaz de cotar entregas com base em "
                "rotas, combustível, pedágios e cotações com "
                "transportadoras."
            ),
        },
    )
    yield TransportStreamChunk(kind="done", payload={})


__all__ = [
    "TransportStreamChunk",
    "run_transport_agent",
]
