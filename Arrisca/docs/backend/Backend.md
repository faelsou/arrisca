# Arrisca — Documentação do Backend

> Plataforma de chat conversacional com IA para o Grupo Arrisca. Multi-tenant, com controle de acesso por área e sensibilidade, RAG sobre a base de conhecimento da empresa e um agente especializado em análise de custos de transporte.
>
> **Versão:** 0.1.0 · **Última atualização:** julho/2026

---

## 1. Visão geral

O backend do Arrisca expõe uma API HTTP (FastAPI) que autentica funcionários via Supabase Auth e oferece um chat com streaming em tempo real. Cada pergunta é classificada por um roteador LLM leve (Claude Haiku) que decide se ela pertence a uma **área de conhecimento** (RH, Financeiro, Operações, Design, Marketing, Comercial, Produção) ou ao **agente de Transporte**. Perguntas de área passam por um pipeline RAG: a pergunta vira embedding, o banco faz busca vetorial **já filtrada pelas permissões do usuário dentro do SQL**, e o Claude Sonnet responde com base exclusivamente nos trechos que aquele usuário pode ver.

O princípio central de segurança do sistema:

> **O LLM nunca recebe no contexto algo que o usuário não pode ver.** O filtro acontece na camada de retrieval, dentro da query SQL. Nem prompt injection vaza dados — o modelo literalmente não os tem no contexto.

Documentos entram no sistema por um pipeline de ingestão assíncrono (Celery): download, extração de texto, chunking por tokens, embeddings em lote e inserção transacional no PostgreSQL com pgvector.

---

## 2. Arquitetura

```
                         ┌─────────────────────────┐
                         │   Frontend (Next.js 14)  │
                         └───────────┬─────────────┘
                                     │ JWT (Supabase) + SSE
                         ┌───────────▼─────────────┐
        ┌───────────────►│      API (FastAPI)       │
        │                │  apps/api                │
        │                └───┬───────────┬─────────┘
        │                    │           │
   Supabase Auth         orchestrator    │ enfileira ingestão
   (valida JWT)          (Haiku router)  │
                             │           ▼
                   ┌─────────┴────┐  ┌──────────┐     ┌─────────┐
                   │  area_agent  │  │  Redis    │◄───►│ Celery  │
                   │  (Sonnet+RAG)│  │  (fila)   │     │ worker  │
                   └──────┬───────┘  └──────────┘     └────┬────┘
                          │                                 │ pipeline de
                          │ busca vetorial                  │ ingestão
                          ▼ com filtro RBAC no SQL          ▼
                   ┌────────────────────────────────────────────┐
                   │        PostgreSQL 16 + pgvector (HNSW)      │
                   │  tenants · users · areas · documents ·      │
                   │  document_chunks · conversations · audit    │
                   └────────────────────────────────────────────┘
```

O contêiner da API e o worker compartilham os pacotes `core/` (serviços de domínio) e `apps/` (entradas), montados como volumes no `docker-compose.yml`. São quatro serviços: `postgres` (imagem `pgvector/pgvector:pg16`, roda a migration automaticamente no primeiro boot), `redis`, `api` e `worker`.

---

## 3. Stack

| Camada | Tecnologia |
| --- | --- |
| API HTTP | Python 3.12 + FastAPI + Uvicorn |
| Streaming | SSE via `sse-starlette` |
| Banco | PostgreSQL 16 + pgvector (índice HNSW) |
| Acesso a dados | asyncpg (sem ORM — SQL explícito) |
| Autenticação | Supabase Auth (JWT HS256, validado com PyJWT) |
| Fila e cache | Redis |
| Jobs assíncronos | Celery |
| LLM de chat | Claude Sonnet (`claude-sonnet-4-6`) |
| LLM de roteamento | Claude Haiku (`claude-haiku-4-5`) |
| Embeddings | OpenAI `text-embedding-3-small` (1536 dimensões) |
| Extração de documentos | pypdf (PDF), python-docx (DOCX) |
| Tokenização | tiktoken (`cl100k_base`) |

---

## 4. Estrutura de diretórios

```
Arrisca/
├── docker-compose.yml          # postgres + redis + api + worker
├── Dockerfile
├── pyproject.toml
├── migrations/
│   └── 001_initial_schema.sql  # schema completo, executado no init do Postgres
├── core/                       # domínio compartilhado entre API e worker
│   ├── db/
│   │   └── pool.py             # pool asyncpg (init/close no lifespan da API)
│   └── services/
│       ├── permissions.py      # ★ FONTE ÚNICA DE VERDADE sobre acessos
│       ├── retrieval.py        # busca vetorial com filtro RBAC no SQL
│       ├── supabase_auth.py    # validação do JWT (HS256)
│       ├── user_context.py     # carrega UserContext a partir do auth_id
│       └── audit.py            # eventos de auditoria append-only
└── apps/
    ├── api/
    │   ├── main.py             # app FastAPI, lifespan, CORS, routers
    │   ├── deps.py             # get_db, get_current_user, require_role
    │   └── routers/
    │       ├── me.py           # GET /me
    │       ├── conversations.py# chat com streaming SSE
    │       ├── documents.py    # upload e listagem de documentos
    │       └── admin.py        # usuários, vínculos, audit log
    ├── agents/
    │   ├── orchestrator.py     # roteia a mensagem (Haiku)
    │   ├── area_agent.py       # RAG + streaming (Sonnet)
    │   └── transport_agent.py  # agente de transporte (STUB — ver §9.3)
    └── worker/
        └── ingestion/
            ├── celery_app.py   # app Celery + task ingest_document
            └── pipeline.py     # download → extração → chunking → embed → insert
```

Regra de ouro do projeto: **nenhuma lógica de permissão fora de `core/services/permissions.py`**. Routers, agentes e retrieval consomem as funções `can_*` e o `build_chunk_visibility_sql` — nunca reimplementam regras.

---

## 5. Modelo de dados

Enums do schema: `primary_role` (`super_admin | executive | manager | employee`), `membership_level` (`employee | manager`), `sensitivity_level` (`public | internal | restricted | confidential`), `agent_type` (`area | transport`), `ingestion_status` (`pending | processing | completed | failed`), `message_role` (`user | assistant | system | tool`).

### Tabelas principais

**`tenants`** — cada empresa cliente. Hoje há um: Grupo Arrisca.

**`users`** — espelho local do usuário do Supabase. `auth_id` referencia (logicamente) `auth.users(id)` do Supabase; senha nunca é armazenada aqui. `primary_role` define o papel global do usuário no tenant.

**`areas`** — as áreas de conhecimento do tenant (RH, Financeiro etc.). A flag `is_transport` marca a área especial atendida pelo agente de transporte.

**`area_memberships`** — vínculo usuário↔área com nível (`employee` ou `manager`) e trilha de quem concedeu (`granted_by`, `granted_at`, `revoked_at` para revogação lógica).

**`documents`** — metadados do documento: área, sensibilidade, `source_uri` (origem do arquivo), status de ingestão, erro legível em `ingestion_error`, versionamento (`version`, `is_current`, `parent_document_id`).

**`document_chunks`** — o coração do RAG. Cada chunk carrega `content`, `embedding vector(1536)`, `chunk_index`, `token_count` e `metadata` (JSONB — para PDFs inclui `page_start`/`page_end`). As colunas `tenant_id`, `area_id`, `sensitivity` e `is_current` são **deliberadamente denormalizadas** do documento pai: o filtro de permissão roda sem JOIN, o que é crítico para o índice HNSW ser usado com eficiência.

Índice vetorial:

```sql
CREATE INDEX idx_chunks_embedding
    ON document_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
```

**`conversations` / `messages` / `message_sources`** — histórico do chat. `message_sources` registra quais chunks embasaram cada resposta do assistente (com similaridade e rank), habilitando citações no frontend.

**`audit_log`** — append-only por construção: triggers no banco bloqueiam `UPDATE` e `DELETE`. Indexado por tenant, usuário e ação.

**`vehicles` / `transport_carriers`** — frota própria (tipo, eixos, combustível, consumo, custo diário) e transportadoras parceiras com `rate_table` em JSONB (base + por kg + ad valorem por destino). Serão consumidas pelo agente de transporte.

---

## 6. Autenticação e autorização

### 6.1 Fluxo de autenticação

O frontend autentica o usuário diretamente no Supabase (e-mail/senha) e recebe um JWT. Toda chamada à API leva `Authorization: Bearer <jwt>`. No backend:

1. `deps.get_current_user` extrai o token do header.
2. `supabase_auth.verify_supabase_jwt` valida assinatura (HS256 com `SUPABASE_JWT_SECRET`), expiração e claims — token inválido → `401`.
3. `user_context.load_user_context` busca o usuário em `public.users` pelo `auth_id` do token e monta o `UserContext` com papel primário e memberships ativas. Usuário do Supabase sem registro em `public.users` → `403` (não provisionado).

O `UserContext` (definido em `permissions.py`) circula por toda a request — routers, orchestrator, retrieval e auditoria.

### 6.2 Modelo RBAC

Duas dimensões independentes que se combinam:

**Papéis** (hierárquicos):

| Papel | Alcance |
| --- | --- |
| `super_admin` | tudo, em qualquer tenant (operação da plataforma) |
| `executive` | tudo dentro do próprio tenant |
| `manager` (na área X) | conteúdo da área X até `restricted` |
| `employee` (na área X) | conteúdo da área X até `internal` |

**Sensibilidade dos documentos:**

| Nível | Quem vê |
| --- | --- |
| `public` | qualquer membro do tenant |
| `internal` | membros da área |
| `restricted` | manager da área ou acima |
| `confidential` | executive ou acima, apenas |

Um usuário pode ter memberships em várias áreas com níveis diferentes (employee em Financeiro, manager em Produção). O papel efetivo sobre um documento é o máximo entre o papel primário e o nível da membership na área daquele documento.

### 6.3 Onde as regras vivem

`core/services/permissions.py` concentra: os enums `Role` e `Sensitivity` com seus rankings; as checagens pontuais (`can_view_area`, `can_upload_document`, `can_change_document_sensitivity`, `can_grant_membership`, `can_change_primary_role`, `can_view_audit_log`, `can_use_transport_agent`); e o gerador de SQL `build_chunk_visibility_sql`, que traduz o `UserContext` em uma cláusula `WHERE` parametrizada usada tanto pelo retrieval quanto pela listagem de documentos. Os routers usam `Depends(require_role(...))` / `require_executive_or_above()` para proteger endpoints administrativos.

---

## 7. RAG: o caminho de uma pergunta

```
POST /conversations/{id}/messages
  │
  ├─ 1. get_current_user valida JWT e monta UserContext
  ├─ 2. Mensagem do usuário é salva imediatamente em `messages`
  ├─ 3. orchestrator.route_message (Claude Haiku, ~200 ms)
  │       → decide: área específica OU transporte
  │       → só considera áreas que o usuário PODE acessar
  ├─ 4a. Se área → area_agent.stream_area_response:
  │       • embed_query gera embedding da pergunta (OpenAI)
  │       • retrieve_chunks: UMA query SQL faz busca vetorial
  │         (`embedding <=> $1::vector`, HNSW) + filtro RBAC +
  │         threshold de similaridade
  │       • chunks viram contexto do Claude Sonnet
  │       • resposta sai token a token via SSE
  │       • ao final: salva mensagem, grava message_sources, audita
  └─ 4b. Se transporte → run_transport_agent (hoje um stub — §9.3)
```

O detalhe que define o sistema está no passo 4a: `retrieval.retrieve_chunks` injeta a cláusula de visibilidade **dentro** da query vetorial. Não existe pós-filtragem em Python — o que sai do banco já é, por construção, o que o usuário pode ver.

```sql
SELECT ..., 1 - (dc.embedding <=> $1::vector) AS similarity
FROM document_chunks dc
WHERE <cláusula gerada por build_chunk_visibility_sql>
  AND 1 - (dc.embedding <=> $1::vector) >= $3   -- threshold
ORDER BY dc.embedding <=> $1::vector             -- usa o índice HNSW
LIMIT $2;
```

---

## 8. Pipeline de ingestão

Implementado em `apps/worker/ingestion/pipeline.py`, executado pela task Celery `ingestion.ingest_document` (disparada automaticamente pelo `POST /documents`).

### Etapas

1. **Marca `processing`** e limpa erro anterior (re-ingestão é idempotente).
2. **Download** do `source_uri`. Suportados: `file://` (caminho montado no contêiner do worker) e `http(s)://` (URL pública ou pré-assinada de S3). Limite de 50 MB (`INGESTION_MAX_BYTES`). `s3://` direto não é suportado — use URL pré-assinada.
3. **Extração de texto**, decidida por `content_type` ou extensão. PDF via pypdf com rastreio do número da página; PDFs com senha ou escaneados (sem texto) falham com mensagem clara. DOCX via python-docx, incluindo tabelas (cada linha vira `cel1 | cel2 | cel3`). TXT/MD com fallback de encoding (UTF-8 → Latin-1).
4. **Chunking** por tokens com tiktoken: alvo de ~800 tokens (`CHUNK_TOKENS`) com overlap de ~100 (`CHUNK_OVERLAP_TOKENS`), respeitando fronteiras de parágrafo. Parágrafos maiores que o limite são fatiados por sentença. O overlap cai para nível de sentença quando nenhum parágrafo inteiro cabe no orçamento. Chunks de PDF carregam `page_start`/`page_end` no metadata.
5. **Embeddings** em lotes de 100 (`EMBEDDING_BATCH_SIZE`) via OpenAI, com validação de contagem e dimensão (1536).
6. **Persistência transacional**: `DELETE` dos chunks antigos do documento + `INSERT` dos novos (com metadados denormalizados do pai) + `UPDATE` do documento para `completed` — tudo ou nada.
7. Em caso de erro: documento marcado `failed` com o motivo legível em `ingestion_error` (consultável via `GET /documents`, sem precisar olhar o Celery).

### Política de retry

Erros de **culpa do documento** (`IngestionError`: formato não suportado, PDF com senha, arquivo vazio, esquema de URI inválido) **não fazem retry** — falham imediatamente e ficam registrados. Erros **transitórios** (rede, OpenAI indisponível, banco fora) entram em retry com backoff exponencial + jitter, até 3 tentativas (teto de 300 s entre elas).

---

## 9. Agentes

### 9.1 Orchestrator (`apps/agents/orchestrator.py`)

Classificador leve usando Claude Haiku. Recebe a mensagem e a lista de áreas **que o usuário pode acessar** (nunca oferece ao modelo uma área proibida) e devolve uma `RoutingDecision`: área-alvo ou transporte. Latência típica ~200 ms.

### 9.2 Area Agent (`apps/agents/area_agent.py`)

Gera o embedding da pergunta, chama `retrieve_chunks`, formata os trechos como contexto e faz streaming da resposta do Claude Sonnet. Emite `StreamChunk`s tipados que o router `conversations.py` converte em eventos SSE. Ao final, a resposta é salva com as citações em `message_sources`.

### 9.3 Transport Agent (`apps/agents/transport_agent.py`) — **STUB**

Hoje responde "ainda não implementado" (sem quebrar o endpoint). A arquitetura prevista: Claude Sonnet em loop de tool-use, onde **toda conta numérica vem de funções Python determinísticas** (rota e distância, combustível, pedágio por eixo, mão de obra, hospedagem, cotação com transportadoras via `transport_carriers.rate_table`) — o LLM entende o pedido, escolhe tools, interpreta resultados e redige; nunca inventa números. A integração com provedores de rota (Qualp / Rotas Brasil) entrará via abstração `RouteCostProvider` em `core/services/`.

---

## 10. Referência da API

Autenticação: todos os endpoints (exceto `/health`) exigem `Authorization: Bearer <jwt-do-supabase>`.

### Saúde e identidade

```
GET /health          → {"status": "ok"}                    (sem auth)
GET /me              → dados do usuário + papéis + áreas
```

### Conversas e chat

```
POST /conversations
     body: { "agent_type": "area" | "transport", ... }
     → cria conversa

GET  /conversations
     → lista conversas do usuário (mais recentes primeiro)

GET  /conversations/{id}/messages
     → histórico da conversa

POST /conversations/{id}/messages
     body: { "content": "..." }
     → resposta em streaming SSE
```

Eventos SSE emitidos pelo `POST .../messages`, na ordem:

| Evento | Payload | Quando |
| --- | --- | --- |
| `routing` | decisão do orquestrador | primeiro evento (transparência/debug) |
| `sources` | metadados dos chunks recuperados | antes da resposta começar |
| `text` | `{"content": "token"}` | cada pedaço da resposta |
| `done` | `{"message_id": "uuid"}` | fim — mensagem persistida |
| `error` | `{"message": "..."}` | falha durante a geração |

### Documentos

```
POST  /documents
      body: { "area_id", "title", "sensitivity", "source_uri", "content_type" }
      → 202-like: cria registro e enfileira ingestão; retorna id + status
      (permissão validada por can_upload_document)

GET   /documents [?area_id=...]
      → lista documentos VISÍVEIS ao usuário (mesmo filtro do retrieval)

PATCH /documents/{id}
      → altera título/sensibilidade (can_change_document_sensitivity)
```

### Administração (executive+)

```
GET    /admin/users
POST   /admin/users
PATCH  /admin/users/{id}/role
POST   /admin/users/{id}/memberships
DELETE /admin/users/{id}/memberships/{area_id}
GET    /admin/audit          (filtros: usuário, ação, período)
```

---

## 11. Auditoria

`core/services/audit.py` registra eventos estruturados em `audit_log`: `chat.query` (quem perguntou o quê, em qual área, quantos chunks retornaram), `doc.upload`, `perm.grant` e correlatos. A tabela é **append-only garantida no banco** — triggers rejeitam `UPDATE`/`DELETE`, então nem um bug na aplicação consegue reescrever a trilha. Consulta via `GET /admin/audit`, restrita por `can_view_audit_log`.

---

## 12. Variáveis de ambiente

| Variável | Obrigatória | Default | Uso |
| --- | --- | --- | --- |
| `DATABASE_URL` | sim | — | Postgres (API e worker) |
| `REDIS_URL` | sim | `redis://redis:6379/0` | fila do Celery |
| `SUPABASE_JWT_SECRET` | sim | — | validação HS256 do JWT (painel Supabase → Settings → API → JWT Secret) |
| `ANTHROPIC_API_KEY` | sim | — | Claude (Sonnet + Haiku), lida pelo SDK |
| `OPENAI_API_KEY` | sim | — | embeddings, lida pelo SDK |
| `CORS_ORIGINS` | não | `http://localhost:3000` | origens permitidas (separadas por vírgula) |
| `LOG_LEVEL` | não | `info` | verbosidade |
| `EMBEDDING_MODEL` | não | `text-embedding-3-small` | modelo de embedding |
| `EMBEDDING_DIM` | não | `1536` | validação de dimensão |
| `CHUNK_TOKENS` | não | `800` | tamanho alvo do chunk |
| `CHUNK_OVERLAP_TOKENS` | não | `100` | overlap entre chunks |
| `EMBEDDING_BATCH_SIZE` | não | `100` | chunks por chamada de embedding |
| `INGESTION_DOWNLOAD_TIMEOUT` | não | `60` | timeout de download (s) |
| `INGESTION_MAX_BYTES` | não | `52428800` | limite de arquivo (50 MB) |

---

## 13. Setup local

Pré-requisitos: Docker (Compose v2 — o comando é `docker compose`, com espaço), um projeto Supabase com Auth ativado, chaves da Anthropic e da OpenAI.

```bash
# 1. Configurar ambiente
cp .env.example .env      # preencher as chaves

# 2. Subir tudo (a migration roda sozinha no primeiro boot do Postgres)
docker compose up -d

# 3. Seed do tenant e das áreas
#    Há um INSERT comentado no fim de migrations/001_initial_schema.sql —
#    descomente e rode, ou execute via psql:
docker compose exec postgres psql -U postgres -d arrisca

# 4. Primeiro usuário (executive)
#    Painel Supabase → Authentication → Users → Add User → copie o UUID
docker compose exec postgres psql -U postgres -d arrisca -c "
INSERT INTO users (auth_id, tenant_id, email, name, primary_role)
VALUES ('<uuid-do-supabase>', '<tenant-id>', 'voce@arrisca.com', 'Seu Nome', 'executive');"
```

A partir daí, usuários são criados via `POST /admin/users`. A API sobe em `http://localhost:8000` (docs interativas em `/docs`); o worker fica escutando a fila.

### Obtendo um JWT para testes (sem frontend)

```bash
curl -X POST 'https://SEU-PROJETO.supabase.co/auth/v1/token?grant_type=password' \
  -H "apikey: SUA_ANON_KEY" -H "Content-Type: application/json" \
  -d '{"email": "voce@arrisca.com", "password": "sua-senha"}'
# → o campo access_token é o JWT
```

---

## 14. Testando a ingestão de ponta a ponta

```bash
# Criar um arquivo de teste DENTRO do contêiner do worker (é ele quem baixa)
docker compose exec worker bash -c \
  'echo "Hospedagem: limite de R\$ 350 por noite em capitais." > /tmp/politica.txt'

# Upload via API
curl -X POST http://localhost:8000/documents \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d "{\"area_id\": \"$AREA\", \"title\": \"Política de Reembolso\",
       \"sensitivity\": \"internal\",
       \"source_uri\": \"file:///tmp/politica.txt\",
       \"content_type\": \"text/plain\"}"

# Acompanhar: pending → processing → completed
curl http://localhost:8000/documents -H "Authorization: Bearer $JWT"

# Conferir os chunks
docker compose exec postgres psql -U postgres -d arrisca -c "
SELECT chunk_index, token_count, left(content, 60) FROM document_chunks;"
```

Teste de RBAC: repita a pergunta no chat com um usuário `employee` de outra área — o contexto deve vir vazio, provando que o `build_chunk_visibility_sql` está filtrando.

---

## 15. Troubleshooting

| Sintoma | Causa provável | Onde olhar |
| --- | --- | --- |
| `401 Token inválido: Not enough segments` | JWT malformado ou placeholder | o token deve ter formato `xxx.yyy.zzz` |
| `401` com token real | `SUPABASE_JWT_SECRET` errado no `.env`, ou token expirado | painel Supabase → Settings → API |
| `403` após login válido | usuário existe no Supabase mas não em `public.users`, ou `auth_id` divergente | `SELECT auth_id, email FROM users;` |
| `ingestion_status = failed` | motivo legível em `ingestion_error` | `GET /documents` ou tabela `documents` |
| Ingestão travada em `pending` | worker fora do ar ou Redis inacessível | `docker compose logs worker` |
| Erro 401 da OpenAI nos logs do worker | `OPENAI_API_KEY` ausente/ inválida | `.env` + rebuild do worker |
| Chat responde sem contexto | nenhum documento `completed` na área, ou usuário sem permissão | tabela `document_chunks` + memberships |

---

## 16. Estado atual e roadmap

**Implementado:** autenticação Supabase, RBAC completo (`permissions.py`), retrieval vetorial com filtro no SQL, chat de área com streaming SSE e citações, roteador Haiku, upload e listagem de documentos, administração de usuários/vínculos, auditoria append-only, pipeline de ingestão completo (PDF/DOCX/TXT com chunking por tokens e embeddings em lote).

**Pendente, em ordem sugerida:**

1. **Agente de Transporte real** — loop de tool-use com tools determinísticas + `RouteCostProvider` (Qualp / Rotas Brasil) em `core/services/`.
2. **Testes automatizados** — prioridade absoluta para `permissions.py` (cobertura 100%) e para o chunking do pipeline.
3. **Webhook do Supabase** — provisionar `public.users` automaticamente no signup.
4. **Upload real de arquivos** — URL pré-assinada de S3 (hoje o `source_uri` precisa vir pronto).
5. **Frontend** — Next.js 14 + chat UI consumindo o SSE.
6. **Cadastro de frota e transportadoras** — rotas admin para `vehicles` e `transport_carriers`.
7. **Job semanal de preços de combustível** (ANP) via Celery Beat.
8. **Operação em escala** — rate limiting nos endpoints de chat, particionamento mensal do `audit_log`, avaliação de reranking no retrieval.