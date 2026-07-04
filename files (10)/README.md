# Arrisca SAAS — Backend

Chat conversacional com IA para o Grupo Arrisca. Cada área (RH, Financeiro,
Operação, Design, Marketing, Comercial, Produção) tem acesso restrito à sua
base de conhecimento. Um agente especializado de Transporte calcula o
custo-benefício entre entrega própria e terceirizada.

## Stack

| Camada | Tecnologia |
| --- | --- |
| API HTTP | Python 3.12 + FastAPI |
| Banco | PostgreSQL 16 + pgvector |
| Auth | Supabase Auth (JWT HS256) |
| Fila + cache | Redis |
| Background jobs | Celery |
| LLM | Claude Sonnet 4.6 (chat) + Haiku 4.5 (router) |
| Embeddings | OpenAI text-embedding-3-small (1536 dim) |

## Estrutura

```
arrisca-saas/
├── docker-compose.yml             ← Postgres+pgvector, Redis, API, Worker
├── Dockerfile
├── pyproject.toml
├── .env.example
├── migrations/
│   └── 001_initial_schema.sql     ← schema completo (rodado no init do Postgres)
├── core/
│   ├── db/
│   │   └── pool.py                ← pool asyncpg + codec do pgvector
│   └── services/
│       ├── permissions.py         ← ★ fonte única de verdade sobre acessos
│       ├── retrieval.py           ← RAG com filtro de permissão no SQL
│       ├── transport.py           ← tools determinísticas do agente de transporte
│       ├── supabase_auth.py       ← validação do JWT do Supabase
│       ├── user_context.py        ← carrega UserContext do banco
│       └── audit.py               ← log de auditoria append-only
└── apps/
    ├── api/
    │   ├── main.py                ← FastAPI app
    │   ├── deps.py                ← get_current_user, get_db, require_role
    │   └── routers/
    │       ├── me.py              ← GET /me
    │       ├── conversations.py   ← chat com streaming SSE
    │       ├── documents.py       ← upload + listagem
    │       └── admin.py           ← usuários, memberships, audit log
    ├── agents/
    │   ├── orchestrator.py        ← decide qual agente/área (LLM router)
    │   ├── area_agent.py          ← chat de área com RAG + streaming
    │   └── transport_agent.py     ← loop tool-use do agente de transporte
    └── worker/
        └── ingestion.py           ← Celery: download, extração, chunking, embedding
```

## Como rodar localmente

### 1. Pré-requisitos
- Docker + Docker Compose
- Conta no Supabase com Auth ativado
- API key da Anthropic e da OpenAI
- (Opcional) Google Maps + Qualp para o agente de transporte

### 2. Configurar env

```bash
cp .env.example .env
# Edite .env preenchendo as chaves
```

### 3. Subir tudo

```bash
docker-compose up
```

O Postgres já vai rodar a migration automaticamente (via volume montado
em `/docker-entrypoint-initdb.d`). A API sobe em http://localhost:8000 e
o worker do Celery fica escutando a fila.

### 4. Criar o tenant e as áreas iniciais

Há um `INSERT` comentado no fim de `migrations/001_initial_schema.sql`.
Descomente e rode, ou execute manualmente via `psql`:

```bash
docker-compose exec postgres psql -U postgres -d arrisca
```

### 5. Criar o primeiro usuário (executive)

No painel do Supabase: Authentication > Users > Add User. Copie o
`id` (UUID) gerado. Depois insira em `public.users`:

```sql
INSERT INTO users (auth_id, tenant_id, email, name, primary_role)
VALUES ('<uuid-do-supabase>', '<tenant-id>', 'voce@arrisca.com', 'Seu Nome', 'executive');
```

A partir daí, usuários novos são criados via API admin (`POST /admin/users`).

## Fluxo de uma pergunta no chat

```
1. Frontend faz POST /conversations/{id}/messages com JWT do Supabase
2. deps.get_current_user valida o JWT, carrega UserContext do banco
3. Mensagem do usuário é salva imediatamente em `messages`
4. orchestrator.route_message classifica a área (Haiku, ~200ms)
5. Se for área:
   - embed_query gera o embedding da pergunta (OpenAI)
   - retrieve_chunks filtra documentos com permissão + busca vetorial (uma query SQL)
   - Claude Sonnet recebe os chunks como contexto e gera resposta em streaming
   - Cada token sai pelo SSE para o frontend
   - Ao fim: salva mensagem do assistente, registra citações e audit log
6. Se for transporte:
   - run_transport_agent entra em loop tool-use
   - Claude chama tools (rota, combustível, pedágio, etc.) com dados reais
   - Resposta final é enviada e salva
```

## Filosofia de segurança

**O LLM nunca recebe no contexto algo que o usuário não pode ver.** Isso
é garantido na camada de retrieval, dentro do SQL. Mesmo prompt injection
não vaza nada — o LLM literalmente não tem os dados sensíveis no contexto.

| Papel primário | Acesso |
| --- | --- |
| `super_admin` | tudo, em qualquer tenant |
| `executive` | tudo dentro do tenant |
| `manager` (área X) | público + interno + restrito em X |
| `employee` (área X) | público + interno em X |

| Sensibilidade | Quem vê |
| --- | --- |
| `public` | qualquer membro do tenant |
| `internal` | membros da área |
| `restricted` | manager+ da área |
| `confidential` | executive+ apenas |

## Endpoints disponíveis

```
GET  /health                                        ← health check
GET  /me                                            ← dados + áreas do usuário

POST   /conversations                               ← cria conversa (area | transport)
GET    /conversations                               ← lista conversas
GET    /conversations/{id}/messages                 ← histórico
POST   /conversations/{id}/messages                 ← envia msg → SSE stream

POST   /documents                                   ← upload (cria + ingestão)
GET    /documents                                   ← lista visíveis
PATCH  /documents/{id}                              ← muda sensibilidade/título

GET    /admin/users                                 ← lista (executive+)
POST   /admin/users                                 ← cria (executive+)
PATCH  /admin/users/{id}/role                       ← muda papel
POST   /admin/users/{id}/memberships                ← concede vínculo a área
DELETE /admin/users/{id}/memberships/{area_id}      ← revoga
GET    /admin/audit                                 ← consulta audit log
```

## O que ainda falta

- Testes (especialmente para `permissions.py` — esse precisa de cobertura 100%)
- Webhook do Supabase para criar `public.users` automaticamente no signup
- Upload de arquivo via URL pré-assinada do S3 (atualmente espera `source_uri` pronto)
- Frontend (Next.js + chat UI)
- Cadastro de veículos e transportadoras (rotas admin)
- Job semanal para atualizar preços de combustível (ANP)
- Particionamento da tabela `audit_log` por mês quando crescer
- Rate limiting nos endpoints de chat (custo de LLM)

Diz qual é o próximo passo.
