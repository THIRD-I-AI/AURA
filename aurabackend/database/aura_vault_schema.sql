-- ======================================================================
-- AURA VAULT — Hybrid Multimodal Database Schema
-- ======================================================================
-- Supports: Regular data, Image embeddings (pgvector), 4D VR spatial (PostGIS)
-- Target:   PostgreSQL 16+ with pgvector and PostGIS extensions
-- Deploy:   Run setup_vault.py or execute directly via psql / pgAdmin
-- ======================================================================

-- ======================== EXTENSIONS ========================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";      -- UUID generation
CREATE EXTENSION IF NOT EXISTS postgis;           -- 4D spatial / VR / GIS
CREATE EXTENSION IF NOT EXISTS vector;            -- AI embeddings / image vectors

-- ======================== REGULAR DATA ========================

-- Users / accounts
CREATE TABLE IF NOT EXISTS users (
    user_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email        TEXT UNIQUE NOT NULL,
    display_name TEXT,
    subscription_tier TEXT CHECK (subscription_tier IN ('free', 'pro', 'enterprise')) DEFAULT 'free',
    metadata     JSONB DEFAULT '{}',
    created_at   TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Data sources registered by users
CREATE TABLE IF NOT EXISTS data_sources (
    source_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id      UUID REFERENCES users(user_id) ON DELETE CASCADE,
    source_type  TEXT NOT NULL,                   -- postgresql, mysql, bigquery, csv, api, etc.
    display_name TEXT NOT NULL,
    config       JSONB NOT NULL DEFAULT '{}',     -- encrypted connection details
    status       TEXT DEFAULT 'active',
    table_count  INTEGER DEFAULT 0,
    last_sync_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Transactions / financial events
CREATE TABLE IF NOT EXISTS transactions (
    tx_id        BIGSERIAL PRIMARY KEY,
    user_id      UUID REFERENCES users(user_id) ON DELETE SET NULL,
    source_id    UUID REFERENCES data_sources(source_id) ON DELETE SET NULL,
    amount       DECIMAL(12, 2),
    currency     VARCHAR(3) DEFAULT 'USD',
    category     TEXT,
    status       TEXT DEFAULT 'completed',
    metadata     JSONB DEFAULT '{}',
    processed_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Audit / event log
CREATE TABLE IF NOT EXISTS audit_log (
    log_id       BIGSERIAL PRIMARY KEY,
    user_id      UUID REFERENCES users(user_id) ON DELETE SET NULL,
    action       TEXT NOT NULL,                   -- query, upload, connect, etc.
    resource     TEXT,                            -- table name, file path, etc.
    details      JSONB DEFAULT '{}',
    ip_address   INET,
    created_at   TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Agent memory — conversation & plan history
CREATE TABLE IF NOT EXISTS agent_memory (
    memory_id    BIGSERIAL PRIMARY KEY,
    session_id   UUID NOT NULL,
    user_id      UUID REFERENCES users(user_id) ON DELETE CASCADE,
    role         TEXT NOT NULL CHECK (role IN ('user', 'agent', 'system')),
    content      TEXT NOT NULL,
    embedding    VECTOR(1536),                    -- semantic search over past conversations
    metadata     JSONB DEFAULT '{}',
    created_at   TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Saved queries / reports
CREATE TABLE IF NOT EXISTS saved_queries (
    query_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id      UUID REFERENCES users(user_id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    sql_text     TEXT NOT NULL,
    description  TEXT,
    chart_config JSONB,
    tags         TEXT[],
    run_count    INTEGER DEFAULT 0,
    last_run_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Pipeline definitions
CREATE TABLE IF NOT EXISTS pipelines (
    pipeline_id  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id      UUID REFERENCES users(user_id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    description  TEXT,
    steps        JSONB NOT NULL DEFAULT '[]',     -- array of pipeline step configs
    schedule     TEXT,                            -- cron expression
    status       TEXT DEFAULT 'draft',
    last_run_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ======================== IMAGE / AI EMBEDDINGS ========================

-- Image assets with vector embeddings for similarity search
CREATE TABLE IF NOT EXISTS image_assets (
    image_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id      UUID REFERENCES users(user_id) ON DELETE SET NULL,
    file_name    TEXT NOT NULL,
    storage_url  TEXT NOT NULL,                   -- local path or object-store URL
    mime_type    TEXT DEFAULT 'image/png',
    file_size    BIGINT,                          -- bytes
    width        INTEGER,
    height       INTEGER,
    embedding    VECTOR(1536),                    -- CLIP / Gemini / OpenAI embedding
    labels       TEXT[],                          -- auto-detected or manual labels
    metadata     JSONB DEFAULT '{}',              -- EXIF, camera, colour histogram, etc.
    created_at   TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Index for fast similarity search (IVFFlat — good up to ~1M rows)
CREATE INDEX IF NOT EXISTS idx_image_embedding
    ON image_assets USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- ======================== 4D VR / SPATIAL DATA ========================

-- VR telemetry: position (x,y,z) + timestamp = 4D
CREATE TABLE IF NOT EXISTS vr_telemetry (
    telemetry_id BIGSERIAL PRIMARY KEY,
    user_id      UUID REFERENCES users(user_id) ON DELETE SET NULL,
    session_id   UUID,
    location     GEOMETRY(POINTZ, 4326),          -- x, y, z coordinates
    velocity     VECTOR(3),                       -- movement direction vector
    orientation  VECTOR(4),                       -- quaternion (w, x, y, z)
    captured_at  TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    metadata     JSONB DEFAULT '{}'               -- headset type, FPS, etc.
);

-- Spatial index for fast region queries
CREATE INDEX IF NOT EXISTS idx_vr_location
    ON vr_telemetry USING GIST (location);

-- VR environments / scenes
CREATE TABLE IF NOT EXISTS vr_environments (
    env_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name         TEXT NOT NULL,
    description  TEXT,
    bounding_box GEOMETRY(POLYGONZ, 4326),        -- 3D bounding region
    config       JSONB DEFAULT '{}',              -- lighting, physics, etc.
    created_at   TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Spatial objects within VR environments
CREATE TABLE IF NOT EXISTS vr_objects (
    object_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    env_id       UUID REFERENCES vr_environments(env_id) ON DELETE CASCADE,
    object_type  TEXT NOT NULL,                   -- mesh, light, collider, etc.
    label        TEXT,
    position     GEOMETRY(POINTZ, 4326),
    scale        VECTOR(3),                       -- x, y, z scale factors
    mesh_embedding VECTOR(512),                   -- 3D shape embedding for similarity
    properties   JSONB DEFAULT '{}',
    created_at   TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ======================== GENERIC VECTOR STORE ========================
-- Catch-all for any future embedding use-case (documents, audio, etc.)

CREATE TABLE IF NOT EXISTS vector_store (
    vector_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    collection   TEXT NOT NULL,                   -- logical grouping: "documents", "audio", etc.
    source_ref   TEXT,                            -- foreign key or URI to the source
    embedding    VECTOR(1536),
    content      TEXT,                            -- optional text representation
    metadata     JSONB DEFAULT '{}',
    created_at   TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_vector_store_collection
    ON vector_store (collection);

CREATE INDEX IF NOT EXISTS idx_vector_store_embedding
    ON vector_store USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- ======================== HELPER VIEWS ========================

-- Cross-domain: link VR movement to purchases
CREATE OR REPLACE VIEW vr_purchase_activity AS
SELECT
    u.email,
    u.display_name,
    ST_AsText(v.location) AS vr_position,
    v.captured_at        AS vr_timestamp,
    t.amount,
    t.currency,
    t.category,
    t.processed_at       AS purchase_time
FROM vr_telemetry v
JOIN users u ON u.user_id = v.user_id
JOIN transactions t ON t.user_id = v.user_id
WHERE t.processed_at BETWEEN v.captured_at - INTERVAL '1 hour'
                         AND v.captured_at + INTERVAL '1 hour';

-- Image search helper: find similar images to a given embedding
-- Usage: SELECT * FROM find_similar_images('<embedding>'::vector, 10);
CREATE OR REPLACE FUNCTION find_similar_images(
    query_embedding VECTOR(1536),
    result_limit    INTEGER DEFAULT 10
)
RETURNS TABLE (
    image_id    UUID,
    file_name   TEXT,
    storage_url TEXT,
    labels      TEXT[],
    similarity  DOUBLE PRECISION
) AS $$
    SELECT
        image_id,
        file_name,
        storage_url,
        labels,
        1 - (embedding <=> query_embedding) AS similarity
    FROM image_assets
    WHERE embedding IS NOT NULL
    ORDER BY embedding <=> query_embedding
    LIMIT result_limit;
$$ LANGUAGE SQL STABLE;

-- ======================== UPDATE TRIGGERS ========================

CREATE OR REPLACE FUNCTION update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER trg_users_updated
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();
