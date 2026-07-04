-- =============================================================================
-- Arrisca SAAS — Schema inicial
-- Postgres 16 + pgvector + Supabase Auth
-- =============================================================================

BEGIN;

-- Extensões
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- =============================================================================
-- Enums
-- =============================================================================

CREATE TYPE primary_role AS ENUM ('super_admin', 'executive', 'manager', 'employee');
CREATE TYPE membership_level AS ENUM ('employee', 'manager');
CREATE TYPE sensitivity_level AS ENUM ('public', 'internal', 'restricted', 'confidential');
CREATE TYPE message_role AS ENUM ('user', 'assistant', 'system', 'tool');
CREATE TYPE agent_type AS ENUM ('area', 'transport');
CREATE TYPE ingestion_status AS ENUM ('pending', 'processing', 'completed', 'failed');

-- =============================================================================
-- Tenants
-- =============================================================================

CREATE TABLE tenants (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        VARCHAR(64)  NOT NULL UNIQUE,
    name        VARCHAR(255) NOT NULL,
    settings    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- Users (integrados com Supabase Auth)
-- auth_id referencia auth.users(id) do schema gerenciado pelo Supabase.
-- Não duplicamos senha aqui — o Supabase cuida disso.
-- =============================================================================

CREATE TABLE users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    auth_id       UUID UNIQUE,                    -- FK lógica para auth.users(id)
    tenant_id     UUID REFERENCES tenants(id) ON DELETE CASCADE,
    email         VARCHAR(255) NOT NULL,
    name          VARCHAR(255) NOT NULL,
    primary_role  primary_role NOT NULL DEFAULT 'employee',
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, email)
);

CREATE INDEX idx_users_tenant ON users(tenant_id) WHERE active = TRUE;
CREATE INDEX idx_users_auth ON users(auth_id);

-- =============================================================================
-- Áreas (RH, Financeiro, etc.) — definidas por tenant
-- =============================================================================

CREATE TABLE areas (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    slug          VARCHAR(64)  NOT NULL,
    name          VARCHAR(255) NOT NULL,
    description   TEXT,
    is_transport  BOOLEAN NOT NULL DEFAULT FALSE,  -- marca a área especial do agente de transporte
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, slug)
);

CREATE INDEX idx_areas_tenant ON areas(tenant_id);

-- =============================================================================
-- Vínculos usuário ↔ área (com nível de permissão na área)
-- =============================================================================

CREATE TABLE area_memberships (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    area_id     UUID NOT NULL REFERENCES areas(id) ON DELETE CASCADE,
    level       membership_level NOT NULL DEFAULT 'employee',
    granted_by  UUID REFERENCES users(id),
    granted_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at  TIMESTAMPTZ,
    UNIQUE (user_id, area_id)
);

CREATE INDEX idx_memberships_user ON area_memberships(user_id) WHERE revoked_at IS NULL;
CREATE INDEX idx_memberships_area ON area_memberships(area_id) WHERE revoked_at IS NULL;

-- =============================================================================
-- Documentos (metadados; conteúdo fica em chunks)
-- =============================================================================

CREATE TABLE documents (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    area_id             UUID NOT NULL REFERENCES areas(id),
    title               VARCHAR(500) NOT NULL,
    sensitivity         sensitivity_level NOT NULL DEFAULT 'internal',
    source_uri          TEXT,                                  -- s3://, file://, https://
    content_type        VARCHAR(64),
    version             INT NOT NULL DEFAULT 1,
    is_current          BOOLEAN NOT NULL DEFAULT TRUE,
    parent_document_id  UUID REFERENCES documents(id),
    uploaded_by         UUID REFERENCES users(id),
    ingestion_status    ingestion_status NOT NULL DEFAULT 'pending',
    ingestion_error     TEXT,
    ingested_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_documents_tenant_area_current
    ON documents(tenant_id, area_id) WHERE is_current = TRUE;
CREATE INDEX idx_documents_status
    ON documents(ingestion_status) WHERE ingestion_status IN ('pending', 'processing');

-- =============================================================================
-- Chunks com vetor (pgvector)
-- ★ As colunas tenant_id, area_id, sensitivity, is_current são DENORMALIZADAS
--   da tabela documents — isso permite o filtro de permissão rodar sem JOIN,
--   o que é crítico para usar o índice HNSW de vector eficientemente.
-- =============================================================================

CREATE TABLE document_chunks (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id   UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tenant_id     UUID NOT NULL,
    area_id       UUID NOT NULL,
    sensitivity   sensitivity_level NOT NULL,
    is_current    BOOLEAN NOT NULL DEFAULT TRUE,
    chunk_index   INT NOT NULL,
    content       TEXT NOT NULL,
    embedding     vector(1536),                                -- text-embedding-3-small
    token_count   INT,
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (document_id, chunk_index)
);

-- Índice vetorial HNSW (mais rápido que IVFFlat para reads, custa mais para writes)
CREATE INDEX idx_chunks_embedding
    ON document_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Índices de filtro — combinados com o HNSW pelo planner
CREATE INDEX idx_chunks_tenant_area_current
    ON document_chunks(tenant_id, area_id) WHERE is_current = TRUE;
CREATE INDEX idx_chunks_sensitivity
    ON document_chunks(sensitivity) WHERE is_current = TRUE;

-- =============================================================================
-- Conversas e mensagens
-- =============================================================================

CREATE TABLE conversations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_type  agent_type NOT NULL DEFAULT 'area',
    title       VARCHAR(500),
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_conversations_user ON conversations(user_id, updated_at DESC);

CREATE TABLE messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            message_role NOT NULL,
    content         TEXT NOT NULL,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,    -- model, tokens, latency, tool_calls
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_messages_conversation ON messages(conversation_id, created_at);

-- Citações: que chunks embasaram cada resposta do assistente
CREATE TABLE message_sources (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id  UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    chunk_id    UUID NOT NULL REFERENCES document_chunks(id),
    similarity  NUMERIC(6, 5),
    rank        INT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_sources_message ON message_sources(message_id);

-- =============================================================================
-- Audit log — append-only
-- =============================================================================

CREATE TABLE audit_log (
    id             BIGSERIAL PRIMARY KEY,
    tenant_id      UUID NOT NULL,
    user_id        UUID,
    action         VARCHAR(64) NOT NULL,                   -- chat.query, doc.upload, perm.grant
    resource_type  VARCHAR(64),
    resource_id    UUID,
    ip_address     INET,
    user_agent     TEXT,
    details        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_tenant_created ON audit_log(tenant_id, created_at DESC);
CREATE INDEX idx_audit_user_created   ON audit_log(user_id, created_at DESC);
CREATE INDEX idx_audit_action         ON audit_log(action);

-- Bloqueia UPDATE/DELETE no audit_log (garante append-only no banco)
CREATE OR REPLACE FUNCTION audit_log_no_modify() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_log é append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_no_modify();
CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_no_modify();

-- =============================================================================
-- Transporte: frota
-- =============================================================================

CREATE TABLE vehicles (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id                   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    plate                       VARCHAR(16) NOT NULL,
    vehicle_type                VARCHAR(64) NOT NULL,                  -- fiorino, vuc, toco, truck, carreta
    axles                       INT NOT NULL DEFAULT 2,                -- para cálculo de pedágio
    fuel_type                   VARCHAR(16) NOT NULL,                  -- diesel, gasoline
    fuel_consumption_km_per_l   NUMERIC(5, 2) NOT NULL,
    capacity_kg                 NUMERIC(10, 2),
    capacity_m3                 NUMERIC(10, 2),
    daily_cost                  NUMERIC(10, 2),                        -- depreciação + capital
    active                      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, plate)
);

-- =============================================================================
-- Transporte: transportadoras parceiras (terceirizadas)
-- rate_table exemplo:
-- {
--   "destinos": {
--     "minas-gerais": { "base": 800, "per_kg": 2.5, "ad_valorem_pct": 0.3 },
--     "sao-paulo":    { "base": 400, "per_kg": 1.8, "ad_valorem_pct": 0.25 }
--   }
-- }
-- =============================================================================

CREATE TABLE transport_carriers (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name                VARCHAR(255) NOT NULL,
    contact_email       VARCHAR(255),
    contact_phone       VARCHAR(32),
    rate_table          JSONB NOT NULL DEFAULT '{}'::jsonb,
    avg_delivery_days   INT,
    rating              NUMERIC(3, 2),
    active              BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- Transporte: orçamentos gerados pelo agente
-- =============================================================================

CREATE TABLE transport_quotes (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id              UUID NOT NULL REFERENCES users(id),
    conversation_id      UUID REFERENCES conversations(id),
    origin_address       TEXT NOT NULL,
    destination_address  TEXT NOT NULL,
    cargo_value          NUMERIC(12, 2),
    cargo_weight_kg      NUMERIC(10, 2),
    cargo_volume_m3      NUMERIC(10, 2),
    distance_km          NUMERIC(8, 2),
    own_transport        JSONB,                                 -- breakdown completo: combustível, pedágio, etc.
    third_party          JSONB,                                 -- lista de cotações de transportadoras
    recommendation       TEXT,                                  -- texto da recomendação do agente
    chosen_option        VARCHAR(32),                           -- 'own', 'third_party', NULL
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_quotes_tenant_created ON transport_quotes(tenant_id, created_at DESC);

-- =============================================================================
-- Trigger genérico para updated_at
-- =============================================================================

CREATE OR REPLACE FUNCTION update_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tenants_updated_at        BEFORE UPDATE ON tenants
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER users_updated_at          BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER documents_updated_at      BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER conversations_updated_at  BEFORE UPDATE ON conversations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- =============================================================================
-- Trigger: sincronizar metadados denormalizados nos chunks
-- Quando documents.sensitivity / area_id / is_current muda, replica nos chunks.
-- Crítico para o filtro de permissão funcionar sempre.
-- =============================================================================

CREATE OR REPLACE FUNCTION sync_chunks_from_document() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.sensitivity IS DISTINCT FROM OLD.sensitivity
       OR NEW.area_id     IS DISTINCT FROM OLD.area_id
       OR NEW.is_current  IS DISTINCT FROM OLD.is_current THEN
        UPDATE document_chunks
        SET sensitivity = NEW.sensitivity,
            area_id     = NEW.area_id,
            is_current  = NEW.is_current
        WHERE document_id = NEW.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER documents_sync_chunks AFTER UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION sync_chunks_from_document();

-- =============================================================================
-- Seed inicial: áreas padrão do tenant Arrisca
-- (rodar em outro script após criar o tenant; deixado comentado aqui como referência)
-- =============================================================================

-- INSERT INTO tenants (slug, name) VALUES ('arrisca', 'Grupo Arrisca');
--
-- WITH t AS (SELECT id FROM tenants WHERE slug = 'arrisca')
-- INSERT INTO areas (tenant_id, slug, name, is_transport)
-- SELECT t.id, v.slug, v.name, v.is_transport FROM t,
-- (VALUES
--     ('financeiro', 'Financeiro',  FALSE),
--     ('rh',         'RH',          FALSE),
--     ('operacao',   'Operação',    FALSE),
--     ('design',     'Design',      FALSE),
--     ('marketing',  'Marketing',   FALSE),
--     ('comercial',  'Comercial',   FALSE),
--     ('producao',   'Produção',    FALSE),
--     ('transporte', 'Transporte',  TRUE)
-- ) AS v(slug, name, is_transport);

COMMIT;
