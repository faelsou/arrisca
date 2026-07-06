# 03 — Autenticação e RBAC na Interface

## 1. Fluxo de autenticação (Supabase Auth)

```
Login (email+senha) → Supabase emite JWT → middleware renova sessão a cada request
→ RSC lê sessão via lib/supabase/server.ts → chamadas à FastAPI com Bearer JWT
→ FastAPI valida JWT (JWKS Supabase) e resolve usuário/papel/área no Postgres
```

Regras:

1. **Sem signup público.** Usuários são criados pelo `super_admin` no painel (convite por email via Supabase `inviteUserByEmail`). A tela de login não tem link de cadastro.
2. **Recuperação de senha** padrão Supabase (`resetPasswordForEmail` → rota `/redefinir-senha` que consome o token).
3. **Logout** limpa sessão Supabase + `queryClient.clear()` + redirect `/login`.
4. Sessão expirada durante uso: o wrapper de API detecta `401`, tenta `refreshSession()` uma vez; falhando, mostra toast "Sua sessão expirou" e redireciona preservando `?next=`.

## 2. Endpoint `/me` — contrato

Fonte única de identidade para a UI. Chamado no layout `(app)` (RSC) e hidratado no TanStack Query (`staleTime: 5min`).

```typescript
// lib/contracts/me.ts
export const MeSchema = z.object({
  id: z.string().uuid(),
  email: z.string().email(),
  full_name: z.string(),
  role: z.enum(["employee", "manager", "executive", "super_admin"]),
  area: z.object({
    id: z.string().uuid(),
    name: z.string(),
    slug: z.string(), // "transporte", "financeiro", ...
  }),
  tenant_id: z.string().uuid(),
  // permissões COMPUTADAS pelo backend (permissions.py) — a UI não deriva nada
  permissions: z.object({
    max_sensitivity: z.enum(["public", "internal", "restricted", "confidential"]),
    visible_area_slugs: z.array(z.string()),
    can_use_transport_agent: z.boolean(),
    can_admin_users: z.boolean(),
    can_admin_documents: z.boolean(),
    can_view_usage_dashboard: z.boolean(),
  }),
});
export type Me = z.infer<typeof MeSchema>;
```

**Importante:** flags booleanas vêm prontas do backend. Se amanhã a regra "manager de Transporte usa o agente" mudar em `permissions.py`, a UI não precisa de deploy — só reflete `can_use_transport_agent`.

## 3. Camadas de gate

| Camada | O que faz | O que NÃO faz |
|---|---|---|
| `middleware.ts` | Autenticado vs. anônimo, refresh de token | Nada de papel |
| Layout `(admin)` RSC | `role in (executive, super_admin)` senão `notFound()` | Não confia nisso como segurança |
| Page `/transporte` RSC | `permissions.can_use_transport_agent` senão `notFound()` | — |
| `RoleGate` / checagens client | Esconder botões/menus | Jamais é a única barreira |
| **FastAPI** | **Decisão real de acesso, sempre** | — |

## 4. RBAC visível na experiência

1. **Sidebar filtrada.** Itens "Transporte" e "Administração" só aparecem se as flags permitirem. Sem itens desabilitados/cadeado — o que não pode, não existe (menos ruído, menos engenharia social interna).
2. **Transparência de escopo no chat.** Acima do composer, um hint discreto: "Respostas baseadas em documentos até *interno* das áreas: *Produção*" (derivado de `max_sensitivity` + `visible_area_slugs`). Evita a percepção de "a IA escondeu algo de mim" — o escopo é explícito.
3. **Citações respeitam o backend.** A UI renderiza apenas as fontes que a API retornou; nunca busca metadados extras de documentos por conta própria.
4. **Admin de usuários:** `super_admin` pode tudo; `executive` visualiza sem editar (a API garante; a UI esconde os botões de edição).

## 5. Multi-tenant

O MVP tem um tenant (Grupo Arrisca), mas a UI já não assume isso: `tenant_id` vem do `/me`, nunca hardcoded; nomes/logo do tenant vêm de `GET /tenant` (nome, logo_url, cor primária opcional) para permitir white-label futuro sem refatoração.

## 6. Casos de borda

| Cenário | Comportamento |
|---|---|
| Usuário desativado com sessão viva | API responde `403 account_disabled` → tela cheia "Conta desativada. Fale com o administrador." + logout |
| Papel rebaixado durante sessão | `/me` refetch (a cada foco de janela) atualiza a sidebar; rota aberta que perdeu permissão → próximo request da página falha com 403 → redirect `/chat` com toast |
| JWT válido mas usuário sem registro no Postgres | API `403 user_not_provisioned` → tela "Seu acesso ainda não foi configurado" |
