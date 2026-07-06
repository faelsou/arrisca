# 07 — Qualidade, Testes e Performance

## 1. Acessibilidade (meta: WCAG 2.1 AA)

1. Navegação completa por teclado; focus ring visível (`--ring`) em todo elemento interativo; ordem de tab natural.
2. Cor nunca é canal único: sensibilidade e área sempre com texto/`aria-label` além da cor.
3. Contraste AA verificado nos pares de token (light e dark) — checagem automatizada no CI com `axe` nos testes de componente.
4. Chat: `aria-live` conforme doc 04 §7; skeletons com `aria-busy`.
5. `prefers-reduced-motion`: desliga cursor piscante, transições de sidebar e qualquer animação de entrada.
6. Formulários com `label` explícito, erros associados via `aria-describedby`.

## 2. Performance

| Métrica | Alvo |
|---|---|
| LCP (rota `/chat` fria) | < 2,0 s |
| Primeiro token visível pós-envio | < 2,0 s (percepção coberta pelo estado "Consultando documentos…") |
| Bundle JS inicial da rota de chat | < 180 kB gzip |
| INP | < 200 ms |

Táticas:

1. RSC agressivo: sidebar, header e histórico inicial renderizados no servidor; só o miolo do chat é client.
2. `next/font` para Inter e JetBrains Mono (self-host, `display: swap`).
3. Highlight de código e `react-markdown` importados com `next/dynamic` apenas quando a primeira mensagem assistant chega.
4. Lista de mensagens virtualizada (`@tanstack/react-virtual`) somente acima de ~60 mensagens; abaixo disso, render direto (virtualização tem custo de complexidade em auto-scroll).
5. Admin em chunk separado (route groups já garantem code-splitting).

## 3. Testes

| Camada | Ferramenta | Cobertura mínima |
|---|---|---|
| Unidade | Vitest | Parser SSE (chunks parciais, eventos desconhecidos, abort), formatadores (moeda centavos→BRL, datas), fábrica de query keys |
| Componente | Testing Library + axe | Composer (Enter/Shift+Enter/Esc), `SensitivityBar` (aria), `CitationCard`, `RoleGate` |
| E2E | Playwright | Fluxos: login→chat→resposta com fontes; manager de Transporte acessa `/transporte` e employee recebe 404; super_admin convida usuário; sync de ingestão exibe job |
| Contrato | Vitest | Fixtures reais da FastAPI validadas contra todos os schemas Zod (quebra de contrato falha o CI) |

Mock de SSE nos testes: servidor de fixture que emite a sequência `conversation → sources → delta×n → done` com atrasos, incluindo cenário de `error` no meio do stream.

## 4. Observabilidade

- **Sentry** (browser + edge): erros de runtime, `ApiError` 5xx, falhas de parse Zod em prod (com `code` do contrato divergente).
- Web Vitals reportados (`useReportWebVitals`) para o backend (`POST /telemetry/vitals`) — sem ferramenta paga no MVP.
- Nenhum conteúdo de mensagem do usuário em logs/telemetria. Apenas metadados (duração do stream, contagem de tokens do evento `usage`, intent do router).

## 5. Segurança no cliente

1. Sem `dangerouslySetInnerHTML`; Markdown sem HTML bruto (doc 04 §4).
2. CSP estrita no `next.config`: `default-src 'self'`; `connect-src` apenas API + Supabase; sem `unsafe-eval`.
3. Tokens Supabase geridos pelo `@supabase/ssr` (cookies httpOnly no fluxo server) — nunca em `localStorage` manual.
4. Nenhum dado sensível em query strings; IDs de conversa são UUIDs opacos.
5. Dependências auditadas no CI (`pnpm audit --prod` bloqueante para severidade alta).

## 6. Definition of Done (por feature)

Uma feature só fecha quando: estados de loading/vazio/erro implementados; navegável por teclado; strings em pt-BR centralizadas; contrato Zod cobrindo toda resposta consumida; teste de componente ou E2E cobrindo o caminho feliz; sem warnings de console; revisado em light e dark.
