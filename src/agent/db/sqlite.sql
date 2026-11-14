-- Application state, prototype: SQLite behind the same ports (ADR 0002).
-- Mirrors postgres.sql table for table, with these translations:
--   uuid -> TEXT, timestamptz -> TEXT (ISO 8601 UTC), jsonb / text[] -> TEXT holding JSON,
--   tsvector -> an FTS5 table kept in step by triggers, vector -> BLOB (float32, cosine in Python).
-- Not here: interactions (the prototype writes JSONL traces), Trio Candidates (off until Q1).
-- The LangGraph checkpointer (SqliteSaver) owns its own tables, in its own file.

PRAGMA foreign_keys = ON;

-- Identity and access (HLD 03) -----------------------------------------------------------------

CREATE TABLE users (
    user_id         TEXT PRIMARY KEY,
    sso_subject     TEXT UNIQUE,
    role            TEXT CHECK (role IN ('executive', 'manager')),
    access_version  INTEGER NOT NULL DEFAULT 1,
    active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE user_permissions (
    user_id     TEXT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    permission  TEXT NOT NULL CHECK (permission IN ('persona_editor')),
    granted_by  TEXT REFERENCES users (user_id),
    granted_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (user_id, permission)
);

CREATE TABLE brand_assignments (
    user_id      TEXT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    brand        TEXT NOT NULL,
    assigned_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (user_id, brand)
);

CREATE TRIGGER brand_assignments_bump_ins AFTER INSERT ON brand_assignments BEGIN
    UPDATE users SET access_version = access_version + 1 WHERE user_id = NEW.user_id;
END;
CREATE TRIGGER brand_assignments_bump_del AFTER DELETE ON brand_assignments BEGIN
    UPDATE users SET access_version = access_version + 1 WHERE user_id = OLD.user_id;
END;

-- Conversations (HLD 01) -----------------------------------------------------------------------

CREATE TABLE conversations (
    conversation_id  TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL REFERENCES users (user_id),
    channel          TEXT NOT NULL CHECK (channel IN ('cli', 'slack', 'teams', 'web')),
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_turn_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX conversations_user ON conversations (user_id, last_turn_at DESC);

-- Saved Reports (HLD 04) -----------------------------------------------------------------------

CREATE TABLE saved_reports (
    report_id        TEXT PRIMARY KEY,
    author_id        TEXT NOT NULL REFERENCES users (user_id),
    conversation_id  TEXT NOT NULL REFERENCES conversations (conversation_id),
    turn_id          TEXT NOT NULL,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    title            TEXT NOT NULL CHECK (length(title) <= 200),
    question         TEXT NOT NULL,
    sql              TEXT NOT NULL,
    markdown         TEXT NOT NULL,
    structured       TEXT NOT NULL CHECK (json_valid(structured)),
    scope_all        INTEGER NOT NULL CHECK (scope_all IN (0, 1)),
    scope_brands     TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(scope_brands)),
    data_as_of       TEXT NOT NULL,
    UNIQUE (conversation_id, turn_id),
    CHECK (NOT (scope_all = 1 AND json_array_length(scope_brands) > 0))
);

CREATE INDEX saved_reports_author ON saved_reports (author_id, created_at DESC);
CREATE INDEX saved_reports_conversation ON saved_reports (author_id, conversation_id);

-- Full-text fallback for matching (external-content FTS5, synced by triggers).
CREATE VIRTUAL TABLE saved_reports_fts USING fts5 (
    title, question, markdown,
    content = 'saved_reports', content_rowid = 'rowid'
);

CREATE TRIGGER saved_reports_fts_ins AFTER INSERT ON saved_reports BEGIN
    INSERT INTO saved_reports_fts (rowid, title, question, markdown)
    VALUES (NEW.rowid, NEW.title, NEW.question, NEW.markdown);
END;
CREATE TRIGGER saved_reports_fts_del AFTER DELETE ON saved_reports BEGIN
    INSERT INTO saved_reports_fts (saved_reports_fts, rowid, title, question, markdown)
    VALUES ('delete', OLD.rowid, OLD.title, OLD.question, OLD.markdown);
END;
CREATE TRIGGER saved_reports_fts_upd AFTER UPDATE ON saved_reports BEGIN
    INSERT INTO saved_reports_fts (saved_reports_fts, rowid, title, question, markdown)
    VALUES ('delete', OLD.rowid, OLD.title, OLD.question, OLD.markdown);
    INSERT INTO saved_reports_fts (rowid, title, question, markdown)
    VALUES (NEW.rowid, NEW.title, NEW.question, NEW.markdown);
END;

CREATE TABLE report_entities (
    report_id    TEXT NOT NULL REFERENCES saved_reports (report_id) ON DELETE CASCADE,
    entity_type  TEXT NOT NULL CHECK (entity_type IN ('brand', 'category', 'product', 'customer')),
    value        TEXT NOT NULL,
    normalized   TEXT NOT NULL,
    PRIMARY KEY (report_id, entity_type, normalized)
);

CREATE INDEX report_entities_match ON report_entities (normalized);

CREATE TABLE deletion_audit (
    audit_id    TEXT PRIMARY KEY,
    plan_id     TEXT NOT NULL UNIQUE,
    author_id   TEXT NOT NULL REFERENCES users (user_id),
    deleted_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    report_ids  TEXT NOT NULL CHECK (json_valid(report_ids) AND json_array_length(report_ids) > 0),
    titles      TEXT NOT NULL CHECK (json_valid(titles)),
    filter      TEXT NOT NULL CHECK (json_valid(filter)),
    CHECK (json_array_length(report_ids) = json_array_length(titles))
);

CREATE INDEX deletion_audit_author ON deletion_audit (author_id, deleted_at DESC);

-- User Profiles (HLD 05) -----------------------------------------------------------------------

CREATE TABLE user_profiles (
    user_id     TEXT PRIMARY KEY REFERENCES users (user_id) ON DELETE CASCADE,
    format      TEXT CHECK (format IS NULL OR json_valid(format)),
    depth       TEXT CHECK (depth IS NULL OR json_valid(depth)),
    charts      TEXT CHECK (charts IS NULL OR json_valid(charts)),
    language    TEXT CHECK (language IS NULL OR json_valid(language)),
    attributes  TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(attributes)),
    notes       TEXT NOT NULL DEFAULT '[]'
                CHECK (json_valid(notes) AND json_array_length(notes) <= 20),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Golden Bucket (HLD 02) -----------------------------------------------------------------------

CREATE TABLE golden_trios (
    trio_id         TEXT NOT NULL,
    version         INTEGER NOT NULL CHECK (version >= 1),
    question        TEXT NOT NULL,
    paraphrases     TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(paraphrases)),
    sql             TEXT NOT NULL,
    method_summary  TEXT NOT NULL,
    parameters      TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(parameters)),
    scope_all       INTEGER NOT NULL CHECK (scope_all IN (0, 1)),
    brands          TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(brands)),
    tables_used     TEXT NOT NULL CHECK (json_valid(tables_used)),
    question_type   TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('active', 'superseded', 'retired')),
    source_uri      TEXT,
    approved_by     TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (trio_id, version)
);

CREATE UNIQUE INDEX golden_trios_one_active ON golden_trios (trio_id) WHERE status = 'active';

CREATE TABLE trio_embeddings (
    trio_id    TEXT NOT NULL,
    version    INTEGER NOT NULL,
    ordinal    INTEGER NOT NULL,
    text       TEXT NOT NULL,
    embedding  BLOB NOT NULL,                             -- 768 x float32, little-endian
    PRIMARY KEY (trio_id, version, ordinal),
    FOREIGN KEY (trio_id, version) REFERENCES golden_trios (trio_id, version) ON DELETE CASCADE
);

-- Persona (HLD 09) -----------------------------------------------------------------------------

CREATE TABLE persona_versions (
    version         INTEGER PRIMARY KEY CHECK (version >= 1),
    persona         TEXT NOT NULL CHECK (json_valid(persona)),
    created_by      TEXT REFERENCES users (user_id),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    path            TEXT NOT NULL CHECK (path IN ('seed', 'chat', 'direct_edit')),
    changed_fields  TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(changed_fields)),
    gate_result     TEXT CHECK (gate_result IS NULL OR json_valid(gate_result)),
    is_active       INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1))
);

CREATE UNIQUE INDEX persona_versions_one_active ON persona_versions (is_active) WHERE is_active = 1;

CREATE TABLE persona_events (
    event_id      TEXT PRIMARY KEY,
    occurred_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    actor_id      TEXT NOT NULL REFERENCES users (user_id),
    kind          TEXT NOT NULL CHECK (kind IN ('published', 'rejected', 'rolled_back')),
    path          TEXT CHECK (path IN ('chat', 'direct_edit')),
    from_version  INTEGER REFERENCES persona_versions (version),
    to_version    INTEGER REFERENCES persona_versions (version),
    gate_result   TEXT CHECK (gate_result IS NULL OR json_valid(gate_result)),
    candidate     TEXT CHECK (candidate IS NULL OR json_valid(candidate))
);
