# 04 — Chat e Streaming (SSE)

O chat é o coração do produto. Esta spec define layout, protocolo de streaming, renderização e o modo Transport Agent.

## 1. Layout

```
┌────────────┬──────────────────────────────────────────┐
│  Sidebar   │  Header: título da conversa · AreaBadge  │
│            ├──────────────────────────────────────────┤
│ + Nova     │                                          │
│ conversa   │   MessageList (scroll-area)              │
│            │     · MessageBubble (user, à direita)    │
│ Conversas  │     · MessageBubble (assistant, Markdown)│
│ recentes   │         └ CitationCard[] (colapsável)    │
│  · ...     │                                          │
│            ├──────────────────────────────────────────┤
│ Admin ▸    │  hint de escopo (sensibilidade/áreas)    │
│ Conta ▸    │  Composer [textarea]        [Enviar/Stop]│
└────────────┴──────────────────────────────────────────┘
```

Responsivo: em `< md`, sidebar vira `Sheet` (drawer). O composer fica fixo no rodapé com `safe-area-inset` no mobile.

## 2. Protocolo SSE

`POST {API_URL}/chat/stream` com `Accept: text/event-stream`. Consumo via `fetch` + `ReadableStream` (não `EventSource`, que não suporta POST nem headers de auth).

### Request

```json
{
  "conversation_id": "uuid | null",
  "message": "string"
}
```

### Eventos (contrato a alinhar com a FastAPI)

| `event` | `data` | Quando |
|---|---|---|
| `conversation` | `{ "conversation_id": "uuid", "title": "..." }` | Primeiro evento em conversa nova |
| `router` | `{ "intent": "rag" \| "transport" \| "smalltalk" }` | Decisão do Haiku router (usado p/ indicador de modo) |
| `sources` | `{ "citations": Citation[] }` | Antes do texto — chunks recuperados pós-RBAC |
| `delta` | `{ "text": "..." }` | Tokens do Sonnet |
| `tool_call` | `{ "name": "google_maps", "label": "Calculando rota..." }` | Apenas Transport Agent |
| `tool_result` | `{ "name": "...", "summary": "..." }` | Idem |
| `usage` | `{ "input_tokens": n, "output_tokens": n }` | Fim |
| `done` | `{ "message_id": "uuid" }` | Fecha o turno |
| `error` | `{ "code": "...", "message": "..." }` | Qualquer falha |

### Cliente (`lib/api/sse.ts`)

```typescript
export async function streamChat(
  body: ChatRequest,
  handlers: {
    onEvent: (event: SseEvent) => void;
    signal: AbortSignal; // botão "Parar geração"
  }
): Promise<void>
```

Parser incremental de `event:`/`data:` sobre o `ReadableStream`, tolerante a chunks parciais (buffer até `\n\n`). Cada `data` validado com Zod discriminated union — evento desconhecido é ignorado com `console.warn`, nunca quebra o stream.

## 3. Máquina de estados do turno

```
idle → submitting → streaming → done
                  ↘ error
streaming --abort--> aborted (mensagem parcial mantida, marcada "interrompida")
```

Regras de UX por estado:

1. `submitting`: mensagem do usuário aparece imediatamente (otimista); composer desabilita; botão vira "Parar".
2. `streaming`: texto renderiza token a token com cursor piscante; auto-scroll **apenas se** o usuário já está no fundo (se rolou para cima, mostrar pílula "↓ Novas mensagens").
3. `error`: bolha de erro inline com "Tentar novamente" (reenvia o mesmo texto); a mensagem do usuário não se perde.
4. Reconexão: SSE não retoma no MVP — em queda de rede durante stream, tratar como `error` com retry manual. (Retomada por `last-event-id` fica como evolução.)

## 4. Renderização de mensagens

- Markdown via `react-markdown` + `remark-gfm`; code blocks com highlight (`shiki` ou `rehype-pretty-code`) e botão copiar.
- Sanitização: nunca `dangerouslySetInnerHTML` sobre conteúdo do modelo; `react-markdown` sem `rehype-raw` (HTML bruto desabilitado).
- Citações inline: se o backend emitir marcadores `[1]`, renderizar como superscript clicável que abre o `CitationCard` correspondente; senão, cards agrupados no rodapé da mensagem sob "Fontes (n)" colapsável.
- `CitationCard`: título do documento, `AreaBadge`, `SensitivityBar`, trecho de ~200 caracteres, link "Abrir no Drive" quando `drive_url` presente.

```typescript
export const CitationSchema = z.object({
  index: z.number(),
  document_id: z.string().uuid(),
  document_title: z.string(),
  area_slug: z.string(),
  sensitivity: z.enum(["public", "internal", "restricted", "confidential"]),
  snippet: z.string(),
  drive_url: z.string().url().nullable(),
});
```

## 5. Modo Transport Agent

Quando `router.intent === "transport"` (ou na página dedicada `/transporte`):

1. Timeline de ferramentas: cada `tool_call` vira uma linha com spinner → check ("Consultando Google Maps ✓", "Calculando pedágios ✓", "Aplicando tabela de combustível ✓").
2. Resposta final acompanha `TransportCostCard`: origem → destino, distância, tempo, e a decomposição de custos (pedágio, combustível, mão de obra, hospedagem, **total**) em fonte mono, alinhamento decimal, formato `R$ 1.234,56` (`Intl.NumberFormat('pt-BR')`).
3. A página `/transporte` oferece, além do chat, um formulário estruturado (origem, destino, tipo de veículo, eixos) que apenas monta o prompt e envia pelo mesmo endpoint — um caminho, sem API paralela.

## 6. Histórico e conversas

- `GET /conversations?cursor=` paginado (infinite query); item mostra título (gerado pelo backend), data relativa (`date-fns/locale/pt-BR`).
- `GET /conversations/{id}/messages` carrega o histórico ao abrir; citações persistidas vêm junto de cada mensagem assistant.
- Ações: renomear (inline), excluir (dialog de confirmação). Excluir → invalidação da lista + redirect se a conversa aberta foi excluída.
- Nova conversa = rota `/chat` limpa; o `conversation_id` chega no primeiro evento SSE e a URL é atualizada com `history.replaceState` para `/chat/{id}` sem remount.

## 7. Acessibilidade específica do chat

- `MessageList` com `aria-live="polite"` num nó espelho que recebe a mensagem completa apenas no `done` (evitar leitura token a token no screen reader).
- Composer: `Enter` envia, `Shift+Enter` quebra linha, `Esc` durante streaming = parar geração; tudo documentado em `aria-keyshortcuts`.
- Foco retorna ao composer após `done`.
