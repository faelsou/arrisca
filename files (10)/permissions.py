"""permissions — Single source of truth de RBAC do sistema.

Responsabilidades:
    * Modelar Role (hierarquia de 4 níveis) e Sensitivity (4 níveis de
      classificação de documentos).
    * Modelar UserContext + AreaMembership (carregados uma vez por
      request a partir de `core.services.user_context.load_user_context`).
    * Expor funções booleanas de alto nível para autorização explícita
      em rotas (`can_use_transport_agent`, `can_upload_document`, etc.).
    * Gerar predicados SQL parametrizados (`build_chunk_visibility_sql`)
      usados pelo retrieval e por listagens de documentos — esta é a
      única forma autorizada de filtrar chunks/documentos por permissão.

Princípio central: o LLM NUNCA é a fronteira de segurança. Toda
restrição é aplicada no banco antes de qualquer linha sair.

SUPOSIÇÕES DE SCHEMA (ajustar se divergir do 001_initial_schema.sql):
    * Enum `sensitivity_level` no Postgres com valores:
        'public', 'internal', 'restricted', 'confidential'
    * Enum `area_role` no Postgres com valores:
        'employee', 'manager', 'executive'
      (super_admin é flag em users.is_super_admin, NÃO um area_role)
    * Tabela `document_chunks` com colunas:
        id, document_id, tenant_id, area_id, sensitivity, is_current,
        content, embedding (vector), chunk_index
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional
from uuid import UUID


# ---------------------------------------------------------------------------
# Hierarquias
# ---------------------------------------------------------------------------

class Role(str, Enum):
    """Papel do usuário em UMA área. Ordem reflete a hierarquia."""
    EMPLOYEE = "employee"
    MANAGER = "manager"
    EXECUTIVE = "executive"
    SUPER_ADMIN = "super_admin"  # virtual: representado por flag global


class Sensitivity(str, Enum):
    """Classificação de um documento. Ordem reflete restrição crescente."""
    PUBLIC = "public"
    INTERNAL = "internal"
    RESTRICTED = "restricted"
    CONFIDENTIAL = "confidential"


_ROLE_RANK: dict[Role, int] = {
    Role.EMPLOYEE: 1,
    Role.MANAGER: 2,
    Role.EXECUTIVE: 3,
    Role.SUPER_ADMIN: 4,
}

_SENSITIVITY_RANK: dict[Sensitivity, int] = {
    Sensitivity.PUBLIC: 1,
    Sensitivity.INTERNAL: 2,
    Sensitivity.RESTRICTED: 3,
    Sensitivity.CONFIDENTIAL: 4,
}

# role -> maior sensitivity que aquele role consegue ler/escrever
# DENTRO da sua área:
#   employee    -> internal     (public + internal)
#   manager     -> restricted   (+ restricted)
#   executive   -> confidential (+ confidential = tudo)
_ROLE_MAX_SENSITIVITY: dict[Role, Sensitivity] = {
    Role.EMPLOYEE: Sensitivity.INTERNAL,
    Role.MANAGER: Sensitivity.RESTRICTED,
    Role.EXECUTIVE: Sensitivity.CONFIDENTIAL,
    Role.SUPER_ADMIN: Sensitivity.CONFIDENTIAL,
}


def role_rank(role: Role) -> int:
    return _ROLE_RANK[role]


def sensitivity_rank(sensitivity: Sensitivity) -> int:
    return _SENSITIVITY_RANK[sensitivity]


def role_satisfies(actual: Role, required: Role) -> bool:
    return _ROLE_RANK[actual] >= _ROLE_RANK[required]


def max_sensitivity_for_role(role: Role) -> Sensitivity:
    return _ROLE_MAX_SENSITIVITY[role]


# ---------------------------------------------------------------------------
# Modelos de contexto
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class AreaMembership:
    """Vínculo de um usuário com uma área, com um role específico."""
    area_id: UUID
    area_slug: str
    role: Role


@dataclass(frozen=True, slots=True)
class UserContext:
    """Tudo que precisamos saber sobre o usuário para autorizar a request.

    Carregado uma vez por request por `load_user_context`. Imutável.
    """
    user_id: UUID
    tenant_id: UUID
    email: str
    is_super_admin: bool
    memberships: tuple[AreaMembership, ...]

    # ---- Helpers de consulta ------------------------------------------------

    def role_in_area(self, area_id: UUID) -> Optional[Role]:
        if self.is_super_admin:
            return Role.SUPER_ADMIN
        for m in self.memberships:
            if m.area_id == area_id:
                return m.role
        return None

    def accessible_areas(self) -> set[UUID]:
        return {m.area_id for m in self.memberships}

    def areas_with_min_role(self, min_role: Role) -> set[UUID]:
        return {
            m.area_id for m in self.memberships
            if role_satisfies(m.role, min_role)
        }

    def is_executive_or_above(self) -> bool:
        if self.is_super_admin:
            return True
        return any(
            role_satisfies(m.role, Role.EXECUTIVE)
            for m in self.memberships
        )

    def is_manager_or_above_in(self, area_id: UUID) -> bool:
        role = self.role_in_area(area_id)
        return role is not None and role_satisfies(role, Role.MANAGER)

    def is_executive_or_above_in(self, area_id: UUID) -> bool:
        role = self.role_in_area(area_id)
        return role is not None and role_satisfies(role, Role.EXECUTIVE)


# ---------------------------------------------------------------------------
# Permissões — leitura
# ---------------------------------------------------------------------------

def can_view_area(ctx: UserContext, area_id: UUID) -> bool:
    """Interagir com o agente da área (qualquer role na área basta)."""
    return ctx.is_super_admin or area_id in ctx.accessible_areas()


def can_view_audit_log(ctx: UserContext) -> bool:
    """Audit log: apenas executive+ em alguma área, ou super_admin."""
    return ctx.is_executive_or_above()


def can_use_transport_agent(
    ctx: UserContext,
    transport_area_id: UUID,
) -> bool:
    """Agente de Transporte: super_admin, qualquer role na área de
    Transporte, ou executive+ em qualquer área (decisão de negócio:
    executivos enxergam custos de transporte para decisão estratégica).
    """
    if ctx.is_super_admin:
        return True
    if transport_area_id in ctx.accessible_areas():
        return True
    return ctx.is_executive_or_above()


# ---------------------------------------------------------------------------
# Permissões — escrita de documentos
# ---------------------------------------------------------------------------

def can_upload_document(
    ctx: UserContext,
    area_id: UUID,
    sensitivity: Sensitivity,
) -> bool:
    """Subir documento em uma área.

    Regras:
        * super_admin pode tudo.
        * Senão precisa ter role na área E ter role suficiente para a
          sensitivity escolhida (employee só public/internal, manager
          até restricted, executive até confidential).

    Não permite que um manager publique um documento como confidential
    (porque ele próprio não consegue lê-lo de volta — seria um
    documento "perdido").
    """
    if ctx.is_super_admin:
        return True
    role = ctx.role_in_area(area_id)
    if role is None:
        return False
    return sensitivity_rank(sensitivity) <= sensitivity_rank(
        max_sensitivity_for_role(role)
    )


def can_change_document_sensitivity(
    ctx: UserContext,
    area_id: UUID,
    current_sensitivity: Sensitivity,
    new_sensitivity: Sensitivity,
) -> bool:
    """Trocar a sensitivity de um documento existente.

    Regras:
        * super_admin pode tudo.
        * Caso contrário precisa ter role na área que consiga ler
          AMBAS as sensitivities (origem e destino). Isso impede,
          por ex., um employee promover um doc 'internal' para
          'confidential' e perder o próprio acesso, ou rebaixar um
          'restricted' que ele nem deveria estar enxergando.
    """
    if ctx.is_super_admin:
        return True
    role = ctx.role_in_area(area_id)
    if role is None:
        return False
    cap = sensitivity_rank(max_sensitivity_for_role(role))
    return (
        sensitivity_rank(current_sensitivity) <= cap
        and sensitivity_rank(new_sensitivity) <= cap
    )


# ---------------------------------------------------------------------------
# Permissões — administração de usuários e áreas
# ---------------------------------------------------------------------------

def can_grant_membership(ctx: UserContext, area_id: UUID) -> bool:
    """Conceder/revogar membership em uma área.

    Regras:
        * super_admin pode em qualquer área.
        * executive na própria área pode conceder/revogar memberships
          ali (delegação local de gestão).
    """
    if ctx.is_super_admin:
        return True
    return ctx.is_executive_or_above_in(area_id)


def can_change_primary_role(
    ctx: UserContext,
    target_user_id: Optional[UUID] = None,
) -> bool:
    """Alterar a `primary_role` (papel global) de um usuário.

    Por ser um campo global da tabela `users`, só super_admin pode mexer.
    Executivos gerenciam papéis dentro de suas áreas via
    `can_grant_membership`, não aqui.

    Argumento `target_user_id` ignorado por enquanto (mantido na
    assinatura para evolução: ex. impedir auto-modificação).
    """
    _ = target_user_id
    return ctx.is_super_admin


# ---------------------------------------------------------------------------
# Predicado SQL — a interface CRÍTICA para segurança
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ChunkVisibilitySQL:
    """Resultado de `build_chunk_visibility_sql`.

    `where_sql` é um fragmento que entra em uma cláusula WHERE,
    composto pelo caller via AND com outros predicados. Usa placeholders
    asyncpg ($N) começando em `start_placeholder`.

    `params` traz os valores na ordem dos placeholders gerados.

    `next_placeholder` indica o próximo $N livre para o caller continuar
    sua própria parametrização depois.
    """
    where_sql: str
    params: list[object]
    next_placeholder: int


def build_chunk_visibility_sql(
    ctx: UserContext,
    *,
    start_placeholder: int,
    chunks_alias: str = "dc",
) -> ChunkVisibilitySQL:
    """Constrói o predicado WHERE que restringe `document_chunks` ao
    universo visível para `ctx`.

    Lógica:
        * Sempre força tenant_id = ctx.tenant_id e is_current = TRUE.
        * Se super_admin: sem restrição adicional dentro do tenant.
        * Caso contrário: para cada nível de role do usuário, monta
          um array de áreas com aquele role e exige
          (area_id IN areas_role) AND (sensitivity <= teto do role).

    Usuário sem nenhuma área => predicado degenera para FALSE
    (nenhum chunk retornável).
    """
    alias = chunks_alias
    params: list[object] = []
    p = start_placeholder

    clauses: list[str] = [
        f"{alias}.tenant_id = ${p}",
        f"{alias}.is_current = TRUE",
    ]
    params.append(ctx.tenant_id)
    p += 1

    if ctx.is_super_admin:
        return ChunkVisibilitySQL(
            where_sql=" AND ".join(clauses),
            params=params,
            next_placeholder=p,
        )

    areas_employee_plus: list[UUID] = []
    areas_manager_plus: list[UUID] = []
    areas_executive_plus: list[UUID] = []

    for m in ctx.memberships:
        rank = _ROLE_RANK[m.role]
        if rank >= _ROLE_RANK[Role.EMPLOYEE]:
            areas_employee_plus.append(m.area_id)
        if rank >= _ROLE_RANK[Role.MANAGER]:
            areas_manager_plus.append(m.area_id)
        if rank >= _ROLE_RANK[Role.EXECUTIVE]:
            areas_executive_plus.append(m.area_id)

    if not areas_employee_plus:
        clauses.append("FALSE")
        return ChunkVisibilitySQL(
            where_sql=" AND ".join(clauses),
            params=params,
            next_placeholder=p,
        )

    visibility_parts: list[str] = []

    # employee+ -> public, internal
    visibility_parts.append(
        f"({alias}.area_id = ANY(${p}::uuid[]) "
        f"AND {alias}.sensitivity IN ('public','internal'))"
    )
    params.append(areas_employee_plus)
    p += 1

    if areas_manager_plus:
        # manager+ adiciona restricted
        visibility_parts.append(
            f"({alias}.area_id = ANY(${p}::uuid[]) "
            f"AND {alias}.sensitivity = 'restricted')"
        )
        params.append(areas_manager_plus)
        p += 1

    if areas_executive_plus:
        # executive+ adiciona confidential
        visibility_parts.append(
            f"({alias}.area_id = ANY(${p}::uuid[]) "
            f"AND {alias}.sensitivity = 'confidential')"
        )
        params.append(areas_executive_plus)
        p += 1

    clauses.append("(" + " OR ".join(visibility_parts) + ")")

    return ChunkVisibilitySQL(
        where_sql=" AND ".join(clauses),
        params=params,
        next_placeholder=p,
    )


__all__ = [
    # Enums
    "Role",
    "Sensitivity",
    # Modelos de contexto
    "AreaMembership",
    "UserContext",
    # Resultado do builder SQL
    "ChunkVisibilitySQL",
    # Helpers de hierarquia
    "role_rank",
    "sensitivity_rank",
    "role_satisfies",
    "max_sensitivity_for_role",
    # Permissões — leitura
    "can_view_area",
    "can_view_audit_log",
    "can_use_transport_agent",
    # Permissões — escrita de documentos
    "can_upload_document",
    "can_change_document_sensitivity",
    # Permissões — administração
    "can_grant_membership",
    "can_change_primary_role",
    # Construtor de SQL
    "build_chunk_visibility_sql",
]