# 01 — Visão Geral e Arquitetura

## 1. Objetivo do produto

Interface web para funcionários do Grupo Arrisca conversarem com uma IA que responde com base nos documentos internos da empresa, respeitando papel (role) e nível de sensibilidade. Um funcionário de Produção vê respostas baseadas apenas nos documentos que seu papel permite; um executivo vê mais; um super_admin administra tudo.

## 2. Personas e papéis

| Papel | Persona típica | O que precisa da UI |
|---|---|---|
| `employee` | Operador de produção, designer, vendedor | Chat rápido, histórico próprio, respostas confiáveis com fontes |
| `manager` | Gerente de área (ex.: Transporte, Financeiro) | Tudo acima + documentos `restricted` da sua área + Transport Agent |
| `executive` | Diretoria | Visão cross-área, documentos `confidential`, dashboards de uso |
| `super_admin` | Rafael / TI | Gestão de usuários, áreas, documentos, ingestão, monitoramento |

## 3. Princípios de arquitetura

1. **Server Components por padrão.** `"use client"` apenas onde há interatividade real (chat, formulários, menus). Layouts, listas estáticas e páginas de leitura são RSC.
2. **RBAC é reflexo, não decisão.** A UI consome `GET /me` (papel, área, permissões computadas) e esconde/mostra elementos. A API rejeita qualquer chamada indevida — a UI nunca é a barreira.
3. **Contratos tipados ponta a ponta.** Todo payload da API FastAPI tem um schema Zod espelhado em `lib/contracts/`. Nada de `any`.
4. **Streaming em primeira classe.** O chat é o coração do produto; a experiência de streaming (SSE) deve ser fluida, cancelável e resiliente a reconexão.
5. **pt-BR nativo.** Todo texto de interface em português brasileiro desde o início (strings centralizadas, preparadas para i18n futura, mas sem biblioteca de i18n no MVP).

## 4. Estrutura de pastas

Dentro de `apps/web/` (alinhado ao monorepo existente `apps/api`, `apps/worker`, `apps/agents`):

```
apps/web/
├── app/
│   ├── (auth)/
│   │   ├── login/page.tsx
│   │   ├── recuperar-senha/page.tsx
│   │   └── layout.tsx                 # layout centrado, sem sidebar
│   ├── (app)/
│   │   ├── layout.tsx                 # shell: sidebar + header (RSC, lê /me)
│   │   ├── chat/
│   │   │   ├── page.tsx               # nova conversa
│   │   │   └── [conversationId]/page.tsx
│   │   ├── transporte/page.tsx        # Transport Agent (manager+ da área)
│   │   ├── historico/page.tsx
│   │   └── conta/page.tsx
│   ├── (admin)/
│   │   ├── layout.tsx                 # guard executive/super_admin
│   │   └── admin/
│   │       ├── usuarios/page.tsx
│   │       ├── documentos/page.tsx
│   │       ├── ingestao/page.tsx      # status Celery / Google Drive sync
│   │       └── areas/page.tsx
│   ├── api/                           # apenas route handlers utilitários (ex.: proxy SSE se necessário)
│   ├── layout.tsx                     # root: fontes, providers, tema
│   └── globals.css
├── components/
│   ├── ui/                            # shadcn/ui gerado
│   ├── chat/                          # MessageList, Composer, CitationCard...
│   ├── layout/                        # Sidebar, Header, AreaBadge...
│   └── admin/
├── lib/
│   ├── api/
│   │   ├── client.ts                  # fetch wrapper com auth + erros tipados
│   │   └── sse.ts                     # consumidor de stream SSE
│   ├── contracts/                     # schemas Zod (me, chat, documents, transport...)
│   ├── supabase/
│   │   ├── client.ts                  # browser
│   │   ├── server.ts                  # RSC / route handlers
│   │   └── middleware.ts
│   └── utils.ts
├── hooks/                             # useChat, useMe, useConversations...
├── stores/                            # zustand: ui.ts (sidebar, tema), chat-draft.ts
├── middleware.ts                      # refresh de sessão + proteção de rotas
└── tailwind.config.ts
```

## 5. Roteamento e proteção

| Grupo | Quem acessa | Mecanismo |
|---|---|---|
| `(auth)` | Não autenticado | `middleware.ts` redireciona autenticado → `/chat` |
| `(app)` | Qualquer papel autenticado | `middleware.ts` redireciona anônimo → `/login` |
| `(admin)` | `executive`, `super_admin` | Layout RSC lê `/me`; papel insuficiente → `notFound()` (404, não 403, para não vazar existência da rota) |
| `/transporte` | `manager+` **e** área Transporte (ou executive+) | Verificação no page RSC via `/me` |

O `middleware.ts` faz apenas duas coisas: renovar o token Supabase (`@supabase/ssr`) e o gate autenticado/anônimo. Verificações de papel ficam nos layouts RSC, onde há acesso ao `/me` com cache.

## 6. Variáveis de ambiente

```
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
NEXT_PUBLIC_API_URL=            # ex.: http://localhost:8000 (FastAPI)
```

Nenhum segredo além da anon key no cliente. Chamadas à FastAPI levam o JWT do Supabase no header `Authorization: Bearer`.

## 7. Decisões registradas (ADRs curtas)

| Decisão | Alternativa rejeitada | Motivo |
|---|---|---|
| SSE consumido direto da FastAPI pelo browser | Proxy via route handler Next | Menos latência e menos um hop; CORS já configurado na API |
| Zustand só para UI efêmera | Redux / contexto global | Estado de servidor já vive no TanStack Query; sobra pouco estado real de cliente |
| 404 para rotas sem permissão | 403 com mensagem | Não revelar a existência de áreas administrativas a papéis inferiores |
| Strings centralizadas sem lib i18n | next-intl no MVP | Produto é interno e pt-BR; a abstração vem quando o SaaS externo se confirmar |
