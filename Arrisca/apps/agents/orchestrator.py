"""Orquestrador: decide para qual agente uma pergunta deve ir.

Estratégia em duas camadas:
    1. Se a conversa tem agent_type='transport' fixado, sempre vai pro
       agente de transporte (não chama o LLM classificador).
    2. Para conversas 'area', usa Claude Haiku (rápido e barato) para
       classificar a área da pergunta — só entre as áreas que o usuário
       tem permissão de acessar.

Importante: a classificação é só um HINT para focar o retrieval, não
um filtro de segurança. A segurança continua na função de retrieval.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

import asyncpg
from anthropic import AsyncAnthropic

from core.services.permissions import UserContext


ROUTER_MODEL = "claude-haiku-4-5-20251001"


@dataclass
class RoutingDecision:
    agent_type: str           # 'area' | 'transport'
    area_id_hint: UUID | None # área detectada (None se ambígua ou geral)
    area_slug: str | None
    reasoning: str | None     # explicação do modelo (útil para debug)


async def route_message(
    message: str,
    user: UserContext,
    conversation_agent_type: str,
    db: asyncpg.Connection,
) -> RoutingDecision:
    """Decide qual agente vai responder e qual área priorizar."""

    # 1. Conversa de transporte: caminho direto, sem chamada ao LLM
    if conversation_agent_type == "transport":
        return RoutingDecision(
            agent_type="transport",
            area_id_hint=None,
            area_slug="transporte",
            reasoning="conversa fixada como transporte",
        )

    # 2. Conversa de área: classifica entre as áreas que o usuário tem acesso
    areas = await _load_user_accessible_areas(db, user)
    if not areas:
        return RoutingDecision(
            agent_type="area",
            area_id_hint=None,
            area_slug=None,
            reasoning="usuário sem áreas vinculadas — busca apenas em públicos",
        )

    if len(areas) == 1:
        # Atalho: só tem uma área, não precisa chamar LLM
        only = areas[0]
        return RoutingDecision(
            agent_type="area",
            area_id_hint=only["id"],
            area_slug=only["slug"],
            reasoning="usuário só tem acesso a uma área",
        )

    decision = await _classify_with_llm(message, areas)
    return decision


async def _load_user_accessible_areas(
    db: asyncpg.Connection,
    user: UserContext,
) -> list[dict]:
    """Retorna áreas que o usuário pode acessar (ignora a área de transporte)."""
    if user.is_super_admin or user.is_executive:
        rows = await db.fetch(
            """
            SELECT id, slug, name, description
            FROM areas
            WHERE tenant_id = $1 AND is_transport = FALSE
            ORDER BY slug
            """,
            user.tenant_id,
        )
    else:
        area_ids = [m.area_id for m in user.area_memberships]
        if not area_ids:
            return []
        rows = await db.fetch(
            """
            SELECT id, slug, name, description
            FROM areas
            WHERE id = ANY($1::uuid[]) AND is_transport = FALSE
            ORDER BY slug
            """,
            area_ids,
        )
    return [dict(r) for r in rows]


async def _classify_with_llm(message: str, areas: list[dict]) -> RoutingDecision:
    """Usa Claude Haiku para escolher a área mais provável da pergunta."""
    client = AsyncAnthropic()

    areas_listing = "\n".join(
        f"- {a['slug']}: {a['name']}" + (f" ({a['description']})" if a['description'] else "")
        for a in areas
    )

    system = f"""Você é um classificador. Recebe uma pergunta de um funcionário
e identifica qual área da empresa pode respondê-la.

Áreas disponíveis:
{areas_listing}

Responda APENAS com JSON neste formato exato, sem nenhum texto antes ou depois:
{{"area_slug": "<slug_da_area_ou_null>", "reasoning": "<frase curta>"}}

Se a pergunta não se encaixa em nenhuma área específica (cumprimento, pergunta
geral, etc.), use "area_slug": null.
"""

    response = await client.messages.create(
        model=ROUTER_MODEL,
        max_tokens=200,
        system=system,
        messages=[{"role": "user", "content": message}],
    )

    raw = ""
    for block in response.content:
        if getattr(block, "type", None) == "text":
            raw += block.text

    try:
        # Tolera blocos de código markdown ao redor do JSON
        cleaned = raw.strip().replace("```json", "").replace("```", "").strip()
        parsed = json.loads(cleaned)
        slug = parsed.get("area_slug")
        reasoning = parsed.get("reasoning")
    except Exception:
        return RoutingDecision(
            agent_type="area",
            area_id_hint=None,
            area_slug=None,
            reasoning=f"falha ao parsear classificação: {raw[:200]}",
        )

    if slug is None:
        return RoutingDecision(
            agent_type="area",
            area_id_hint=None,
            area_slug=None,
            reasoning=reasoning,
        )

    matched = next((a for a in areas if a["slug"] == slug), None)
    if not matched:
        return RoutingDecision(
            agent_type="area",
            area_id_hint=None,
            area_slug=None,
            reasoning=f"slug '{slug}' não encontrado nas áreas do usuário",
        )

    return RoutingDecision(
        agent_type="area",
        area_id_hint=matched["id"],
        area_slug=matched["slug"],
        reasoning=reasoning,
    )
