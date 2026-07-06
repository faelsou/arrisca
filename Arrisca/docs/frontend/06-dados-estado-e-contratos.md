# 06 — Dados, Estado e Contratos

## 1. Cliente de API (`lib/api/client.ts`)

Wrapper único sobre `fetch`. Responsabilidades:

```typescript
export async function api<T>(
  path: string,
  opts: {
    method?: "GET" | "POST" | "PATCH" | "DELETE";
    body?: unknown;
    schema: z.ZodType<T>;   // validação obrigatória da resposta
    signal?: AbortSignal;
  }
): Promise<T>
```

1. Injeta `Authorization: Bearer {access_token}` do Supabase (browser: client; RSC: server client).
2. `401` → um `refreshSession()` + retry único → falhou, `SessionExpiredError`.
3. Erros da FastAPI seguem envelope `{ "detail": { "code": string, "message": string } }` → lançados como `ApiError(code, message, status)` tipado.
4. Resposta sempre validada com o schema Zod — divergência de contrato falha alto em dev (`throw`) e loga + retorna parse "safe" degradado em prod.

## 2. Convenções TanStack Query

### Query keys (fábrica central, `lib/api/keys.ts`)

```typescript
export const qk = {
  me: ["me"] as const,
  tenant: ["tenant"] as const,
  conversations: (cursor?: string) => ["conversations", { cursor }] as const,
  conversation: (id: string) => ["conversations", id] as const,
  messages: (conversationId: string) => ["conversations", conversationId, "messages"] as const,
  adminUsers: (filters: UserFilters) => ["admin", "users", filters] as const,
  adminDocuments: (filters: DocFilters) => ["admin", "documents", filters] as const,
  ingestionJobs: ["admin", "ingestion", "jobs"] as const,
  areas: ["areas"] as const,
};
```

### Políticas

| Recurso | `staleTime` | Observações |
|---|---|---|
| `me`, `tenant`, `areas` | 5 min | `refetchOnWindowFocus: true` para `me` (captura mudança de papel) |
| `conversations` | 30 s | Infinite query por cursor |
| `messages` | ∞ até invalidação | Mensagens novas entram via SSE no cache (`setQueryData`), não via refetch |
| `ingestionJobs` | 0 | `refetchInterval: 4000` condicional a job ativo |
| Admin (users/docs) | 30 s | Invalidação após qualquer mutação |

### Mutações

Padrão otimista somente onde a reversão é trivial (renomear conversa, toggle de status de usuário). Demais mutações: pessimistas com loading no botão. Toda mutação invalida as keys afetadas explicitamente — nada de `invalidateQueries()` global.

## 3. SSE × cache

O stream não passa pelo TanStack Query. Fluxo:

1. `useChat(conversationId)` mantém o turno corrente em `useState` local (rascunho da resposta em streaming).
2. No evento `done`, a mensagem completa (com citações) é gravada em `qk.messages(id)` via `setQueryData` e o estado local é limpo.
3. Em conversa nova, o evento `conversation` cria a entrada no cache de `conversations` (prepend) e atualiza a URL.

Assim o histórico persiste em um só lugar (cache) e o streaming vive apenas durante o turno.

## 4. Estado de cliente (Zustand)

Dois stores pequenos, nada mais:

```typescript
// stores/ui.ts — persistido em localStorage
{ sidebarCollapsed: boolean; theme: "light" | "dark" | "system" }

// stores/chat-draft.ts — em memória
{ drafts: Record<conversationId, string> } // texto não enviado por conversa
```

Qualquer tentação de colocar dados de servidor no Zustand é sinal de erro de modelagem.

## 5. Contratos Zod — índice completo (`lib/contracts/`)

| Arquivo | Schemas |
|---|---|
| `me.ts` | `MeSchema` (doc 03) |
| `tenant.ts` | `TenantSchema { id, name, logo_url?, primary_color? }` |
| `chat.ts` | `ChatRequest`, `SseEvent` (discriminated union por `event`), `CitationSchema` |
| `conversations.ts` | `ConversationSchema { id, title, updated_at, last_intent? }`, `MessageSchema { id, role, content, citations[], created_at }`, paginação `{ items, next_cursor }` |
| `admin-users.ts` | `AdminUserSchema { id, full_name, email, role, area, is_active, last_seen_at? }`, `InviteUserRequest` |
| `admin-documents.ts` | `AdminDocumentSchema { id, title, area_slug, sensitivity, drive_path, chunk_count, ingestion_status, updated_at }` |
| `ingestion.ts` | `IngestionJobSchema { id, status, total, processed, errors: { document_title, message }[], started_at, finished_at? }` |
| `transport.ts` | `TransportCostSchema { origin, destination, distance_km, duration_min, tolls, fuel, labor, lodging?, total }` (todos os valores em centavos, formatação só na borda da UI) |

**Regra de ouro:** valores monetários trafegam como inteiros em centavos; datas como ISO 8601 UTC; a UI formata com `Intl` e `date-fns` no locale `pt-BR`. Nenhuma formatação no backend, nenhum parse frouxo no frontend.

## 6. Tratamento de erros — taxonomia

| `code` da API | Reação da UI |
|---|---|
| `unauthorized` | Refresh + retry → logout |
| `account_disabled` | Tela cheia dedicada |
| `user_not_provisioned` | Tela cheia dedicada |
| `forbidden` | Toast + redirect `/chat` |
| `rate_limited` | Toast "Muitas requisições, aguarde alguns segundos" com `retry_after` |
| `validation_error` | Mapear para campos do formulário (react-hook-form `setError`) |
| desconhecido / 5xx | Alert genérico + "Tentar novamente"; reportar (Sentry, doc 07) |
