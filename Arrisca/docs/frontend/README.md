# Arrisca — Especificações de Frontend

Especificações completas para o desenvolvimento do frontend da plataforma Arrisca: sistema conversacional multi-tenant com RAG e RBAC para o Grupo Arrisca.

## Stack alvo

| Camada | Tecnologia |
|---|---|
| Framework | Next.js 14 (App Router) + TypeScript strict |
| Estilo | Tailwind CSS + shadcn/ui |
| Estado servidor | TanStack Query v5 |
| Estado cliente | Zustand (mínimo necessário) |
| Auth | Supabase Auth (`@supabase/ssr`) |
| Streaming | SSE via `fetch` + `ReadableStream` |
| Validação | Zod (contratos espelhados do backend FastAPI) |

## Documentos

| Arquivo | Conteúdo |
|---|---|
| `01-visao-geral-e-arquitetura.md` | Escopo, personas, princípios, estrutura de pastas, roteamento |
| `02-design-system.md` | Tokens, tipografia, cores, tema, componentes base |
| `03-autenticacao-e-rbac.md` | Fluxo Supabase Auth, middleware, RBAC na UI, sensibilidade |
| `04-chat-e-streaming.md` | Interface de chat, protocolo SSE, citações, Transport Agent |
| `05-paginas-e-fluxos.md` | Especificação página a página, por papel |
| `06-dados-estado-e-contratos.md` | Cliente de API, TanStack Query, contratos Zod |
| `07-qualidade-testes-e-performance.md` | A11y, performance, testes, i18n |

## Princípio central

O frontend **nunca decide permissão** — apenas reflete o que o backend (`permissions.py`) já decidiu. Toda ocultação de UI por papel é conveniência de UX, não segurança. A API é a única fonte de verdade de acesso.

## Ordem de implementação sugerida

1. Design system + layout shell (semana 1)
2. Auth completo + middleware + guards (semana 1–2)
3. Chat com SSE + citações (semana 2–3)
4. Histórico de conversas + áreas (semana 3)
5. Admin: usuários, documentos, ingestão (semana 4)
6. Transport Agent UI (semana 4–5)
7. Polimento: a11y, testes, performance (semana 5–6)
