-- Application state, production: Cloud SQL for PostgreSQL + pgvector (ADR 0002).
-- Explained in docs/lld/02-data.md; ER diagram in ER.md next to this file.
-- The LangGraph checkpointer (PostgresSaver) creates and owns its own tables.
-- Internal IDs only: names and emails stay in the SSO provider.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

-- ---------------------------------------------------------------------------------------------
-- Identity and access (HLD 03)
-- ---------------------------------------------------------------------------------------------

CREATE TABLE users (
    user_id         text PRIMARY KEY,                     -- internal ID, e.g. 'ceo'
    sso_subject     text UNIQUE,                          -- SSO 'sub'; NULL for seeded Users
    role            text CHECK (role IN ('executive', 'manager')),  -- NULL: default deny
    access_version  integer NOT NULL DEFAULT 1,           -- bumped on any role/Brand change
    active          boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE user_permissions (
    user_id     text NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    permission  text NOT NULL CHECK (permission IN ('persona_editor')),
    granted_by  text REFERENCES users (user_id),
    granted_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, permission)
);

CREATE TABLE brand_assignments (
    user_id      text NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    brand        text NOT NULL,                           -- a thelook products.brand value
    assigned_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, brand)
);

-- Keep users.access_version in step with every Brand Assignment change, so a change of access
-- applies at once (HLD 03 § Identity and roles).
CREATE FUNCTION bump_access_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE users SET access_version = access_version + 1
     WHERE user_id = COALESCE(NEW.user_id, OLD.user_id);
    RETURN NULL;
END $$;

CREATE TRIGGER brand_assignments_bump
    AFTER INSERT OR UPDATE OR DELETE ON brand_assignments
    FOR EACH ROW EXECUTE FUNCTION bump_access_version();

-- ---------------------------------------------------------------------------------------------
-- Conversations (HLD 01 § Conversation state). conversation_id = LangGraph thread_id.
-- ---------------------------------------------------------------------------------------------

CREATE TABLE conversations (
    conversation_id  uuid PRIMARY KEY,
    user_id          text NOT NULL REFERENCES users (user_id),
    channel          text NOT NULL CHECK (channel IN ('cli', 'slack', 'teams', 'web')),
    created_at       timestamptz NOT NULL DEFAULT now(),
    last_turn_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX conversations_user ON conversations (user_id, last_turn_at DESC);

-- ---------------------------------------------------------------------------------------------
-- Saved Reports (HLD 04). Private to the Author. Hard delete.
-- ---------------------------------------------------------------------------------------------

CREATE TABLE saved_reports (
    report_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    author_id        text NOT NULL REFERENCES users (user_id),
    conversation_id  uuid NOT NULL REFERENCES conversations (conversation_id),
    turn_id          text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    title            text NOT NULL CHECK (length(title) <= 200),
    question         text NOT NULL,
    sql              text NOT NULL,
    markdown         text NOT NULL,
    structured       jsonb NOT NULL,                      -- ReportStructured
    scope_all        boolean NOT NULL,                    -- Executive query: all Brands
    scope_brands     text[] NOT NULL DEFAULT '{}',
    data_as_of       date NOT NULL,
    search           tsvector GENERATED ALWAYS AS (
                         to_tsvector('english', title || ' ' || question || ' ' || markdown)
                     ) STORED,
    UNIQUE (conversation_id, turn_id),                    -- save_report idempotency
    CHECK (NOT (scope_all AND cardinality(scope_brands) > 0))
);

CREATE INDEX saved_reports_author ON saved_reports (author_id, created_at DESC);
CREATE INDEX saved_reports_conversation ON saved_reports (author_id, conversation_id);
CREATE INDEX saved_reports_search ON saved_reports USING gin (search);

CREATE TABLE report_entities (
    report_id    uuid NOT NULL REFERENCES saved_reports (report_id) ON DELETE CASCADE,
    entity_type  text NOT NULL CHECK (entity_type IN ('brand', 'category', 'product', 'customer')),
    value        text NOT NULL,
    normalized   text NOT NULL,                           -- case- and alias-normalized
    PRIMARY KEY (report_id, entity_type, normalized)
);

CREATE INDEX report_entities_match ON report_entities (normalized);

-- One row per executed Deletion Plan. Metadata only, never content. No FK to saved_reports:
-- the reports are gone. plan_id UNIQUE makes execute_deletion idempotent.
CREATE TABLE deletion_audit (
    audit_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id     uuid NOT NULL UNIQUE,
    author_id   text NOT NULL REFERENCES users (user_id),
    deleted_at  timestamptz NOT NULL DEFAULT now(),
    report_ids  uuid[] NOT NULL CHECK (cardinality(report_ids) > 0),
    titles      text[] NOT NULL,
    filter      jsonb NOT NULL,                           -- DeletionFilter, for the record
    CHECK (cardinality(report_ids) = cardinality(titles))
);

CREATE INDEX deletion_audit_author ON deletion_audit (author_id, deleted_at DESC);

-- ---------------------------------------------------------------------------------------------
-- User Profiles (HLD 05)
-- ---------------------------------------------------------------------------------------------

CREATE TABLE user_profiles (
    user_id     text PRIMARY KEY REFERENCES users (user_id) ON DELETE CASCADE,
    format      jsonb,          -- ProfileValue: {value, source, confidence, confirmed_at}
    depth       jsonb,
    charts      jsonb,
    language    jsonb,
    attributes  jsonb NOT NULL DEFAULT '{}',              -- {name: ProfileValue}
    notes       jsonb NOT NULL DEFAULT '[]'               -- [ProfileNote], at most 20
                CHECK (jsonb_typeof(notes) = 'array' AND jsonb_array_length(notes) <= 20),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------------------------
-- Golden Bucket (HLD 02, ADR 0005). One Trio = one retrieval unit; versions are immutable.
-- ---------------------------------------------------------------------------------------------

CREATE TABLE golden_trios (
    trio_id         text NOT NULL,
    version         integer NOT NULL CHECK (version >= 1),
    question        text NOT NULL,
    paraphrases     text[] NOT NULL DEFAULT '{}',
    sql             text NOT NULL,
    method_summary  text NOT NULL,
    parameters      jsonb NOT NULL DEFAULT '[]',          -- [TrioParameter]
    scope_all       boolean NOT NULL,                     -- untagged: Executives only
    brands          text[] NOT NULL DEFAULT '{}',
    tables_used     text[] NOT NULL,
    question_type   text NOT NULL,
    status          text NOT NULL CHECK (status IN ('active', 'superseded', 'retired')),
    source_uri      text,
    approved_by     text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    search          tsvector GENERATED ALWAYS AS (
                        to_tsvector('english', question || ' ' || method_summary)
                    ) STORED,
    PRIMARY KEY (trio_id, version)
);

CREATE UNIQUE INDEX golden_trios_one_active ON golden_trios (trio_id) WHERE status = 'active';
CREATE INDEX golden_trios_brands ON golden_trios USING gin (brands) WHERE status = 'active';
CREATE INDEX golden_trios_search ON golden_trios USING gin (search);

-- The question and each paraphrase get their own vector, so any of them can match.
-- 768 dims: the Gemini embedding model truncated with output_dimensionality, which keeps HNSW
-- inside pgvector's 2,000-dimension index limit.
CREATE TABLE trio_embeddings (
    trio_id    text NOT NULL,
    version    integer NOT NULL,
    ordinal    smallint NOT NULL,                         -- 0 = question, 1.. = paraphrases
    text       text NOT NULL,
    embedding  vector(768) NOT NULL,
    PRIMARY KEY (trio_id, version, ordinal),
    FOREIGN KEY (trio_id, version) REFERENCES golden_trios (trio_id, version) ON DELETE CASCADE
);

CREATE INDEX trio_embeddings_hnsw ON trio_embeddings USING hnsw (embedding vector_cosine_ops);

-- Trio Candidates: off until client question Q1 is answered (HLD 12). Their table is added with
-- that slice; deleting a Saved Report will then delete its pending Candidates (HLD 04).

-- ---------------------------------------------------------------------------------------------
-- Persona (HLD 09). Immutable versions, one active pointer.
-- ---------------------------------------------------------------------------------------------

CREATE TABLE persona_versions (
    version         integer PRIMARY KEY CHECK (version >= 1),
    persona         jsonb NOT NULL,                       -- Persona
    created_by      text REFERENCES users (user_id),      -- NULL for the seed
    created_at      timestamptz NOT NULL DEFAULT now(),
    path            text NOT NULL CHECK (path IN ('seed', 'chat', 'direct_edit')),
    changed_fields  text[] NOT NULL DEFAULT '{}',
    gate_result     jsonb,                                -- PersonaGateResult; NULL for the seed
    is_active       boolean NOT NULL DEFAULT false
);

CREATE UNIQUE INDEX persona_versions_one_active ON persona_versions (is_active) WHERE is_active;

-- Who, when, which path and what: every publish, gate rejection and rollback.
CREATE TABLE persona_events (
    event_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    occurred_at   timestamptz NOT NULL DEFAULT now(),
    actor_id      text NOT NULL REFERENCES users (user_id),
    kind          text NOT NULL CHECK (kind IN ('published', 'rejected', 'rolled_back')),
    path          text CHECK (path IN ('chat', 'direct_edit')),  -- Admin UI = direct_edit
    from_version  integer REFERENCES persona_versions (version),
    to_version    integer REFERENCES persona_versions (version),  -- NULL when rejected
    gate_result   jsonb,
    candidate     jsonb                                   -- the rejected Persona, for review
);

-- ---------------------------------------------------------------------------------------------
-- Interaction store (HLD 05). Written from turn.completed; PII-redacted on write.
-- Raw rows kept 90 days; anonymized aggregates live elsewhere. Not in the prototype (JSONL).
-- ---------------------------------------------------------------------------------------------

CREATE TABLE interactions (
    trace_id         text PRIMARY KEY,                    -- = reference ID
    conversation_id  uuid NOT NULL REFERENCES conversations (conversation_id),
    user_id          text NOT NULL REFERENCES users (user_id),
    created_at       timestamptz NOT NULL DEFAULT now(),
    role             text,
    channel          text NOT NULL,
    action           text,                                -- router action, or the block verdict
    question         text NOT NULL,                       -- redacted
    sql_attempts     jsonb NOT NULL DEFAULT '[]',         -- [{sql, rewritten, guard, error}]
    result_shape     jsonb,                               -- columns + row count, never rows
    answer           text,                                -- redacted
    trios_used       text[] NOT NULL DEFAULT '{}',        -- trio_id@version
    retrieval_gap    boolean NOT NULL DEFAULT false,
    persona_version  integer REFERENCES persona_versions (version),
    outcome          text NOT NULL,
    error_class      text,
    feedback         smallint CHECK (feedback IN (-1, 1)),
    tokens           integer,
    cost_usd         numeric(10, 6)
);

CREATE INDEX interactions_created ON interactions (created_at);
