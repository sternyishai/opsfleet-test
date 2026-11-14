# 02 · Data schemas

The application state, table by table. The DDL is the source of truth:

- [`src/agent/db/postgres.sql`](../../src/agent/db/postgres.sql): production (Cloud SQL + pgvector, [ADR 0002](../adr/0002-postgres-for-app-state.md)). Verified by loading it into `pgvector/pgvector:pg17`.
- [`src/agent/db/sqlite.sql`](../../src/agent/db/sqlite.sql): the prototype, behind the same ports. Loaded by `pytest` on every run.
- [`src/agent/db/ER.md`](../../src/agent/db/ER.md): the ER diagram.

The analytics data (`orders`, `order_items`, `products`, `users` in BigQuery) is not ours and is not here. Note the name clash: the BigQuery `users` table holds **Customers**; our `users` table holds **Users**.

## Tables

| Table | Holds | Key rules | HLD |
|---|---|---|---|
| `users` | Internal User ID, SSO subject, role, `access_version` | `role` NULL means default deny. No names or emails. | [03](../hld/03-safety-and-pii.md#identity-and-roles) |
| `user_permissions` | `persona_editor` grants | One row per (User, permission); the CHECK lists every permission that exists | [09](../hld/09-persona-management.md#who-may-change-it) |
| `brand_assignments` | Manager ↔ Brand | A trigger bumps `users.access_version` on every change, so access changes apply at once | [03](../hld/03-safety-and-pii.md#identity-and-roles) |
| `conversations` | One channel thread | `conversation_id` is the LangGraph `thread_id` | [01](../hld/01-architecture.md#conversation-state) |
| `saved_reports` | Question, SQL, answer, structured part, scope | `UNIQUE (conversation_id, turn_id)` makes `save_report` idempotent. `scope_all` / `scope_brands` record the guard's scope. Hard delete. | [04](../hld/04-destructive-ops.md#saved-reports) |
| `report_entities` | Brands, categories, products, Customer IDs named in a report | Matched on `normalized`; cascades with the report | [04](../hld/04-destructive-ops.md) |
| `deletion_audit` | Who deleted what, when | Metadata only. `plan_id UNIQUE` makes `execute_deletion` idempotent. No FK to the reports, which are gone. | [04](../hld/04-destructive-ops.md#executing-the-deletion) |
| `user_profiles` | Format, depth, charts, language, attributes, notes | Each typed value is a `ProfileValue` JSON (`value`, `source`, `confidence`, `confirmed_at`). At most 20 notes, enforced by CHECK. | [05](../hld/05-learning-loop.md#what-is-stored) |
| `golden_trios` | Stored Trios, versioned | At most one `active` version per `trio_id` (partial unique index). `scope_all` = untagged, Executives only. | [02](../hld/02-hybrid-intelligence.md#golden-trio-record) |
| `trio_embeddings` | One vector per question and per paraphrase | 768 dimensions | [02](../hld/02-hybrid-intelligence.md) |
| `persona_versions` | Immutable Persona versions | Exactly one `is_active` (partial unique index). The gate result is stored with the version. | [09](../hld/09-persona-management.md#publishing-and-rollback) |
| `persona_events` | Every publish, gate rejection and rollback | Who, when, which path, from and to version | [09](../hld/09-persona-management.md#publishing-and-rollback) |
| `interactions` | The interaction store: question, SQL attempts, result *shape*, answer, feedback | Production only; PII-redacted on write; keyed by `trace_id` | [05](../hld/05-learning-loop.md#raw-material-the-interaction-store) |

## Decisions made in the DDL

- **Scope is two columns, not a magic value.** `scope_all boolean` plus `scope_brands text[]`, with a CHECK that they are not both set. "All Brands" is an Executive's report; an empty list with `scope_all = false` is a report over no Brand. This maps one to one onto `BrandScope` ([domain/identity.py](../../src/agent/domain/identity.py)) and makes the re-check on use a set comparison.
- **The access version lives in the database.** A trigger bumps it, so no admin path can change Brand Assignments without the agent noticing at the next turn.
- **One vector per paraphrase.** HLD 02 embeds the question and its paraphrases. A separate `trio_embeddings` table lets each one match on its own; the best match per Trio counts.
- **768 dimensions.** pgvector's HNSW index takes at most 2,000 dimensions, and the Gemini embedding model can be truncated with `output_dimensionality`. 768 keeps the index and the cost small; it is a setting to revisit if retrieval quality needs more.
- **Full text, two ways.** Postgres uses a generated `tsvector` with a GIN index; SQLite uses an external-content FTS5 table kept in step by triggers. Both index title, question and answer.
- **Idempotency is a constraint.** `save_report` and `execute_deletion` are idempotent because the database refuses the second write (`UNIQUE`), not because the code remembers.
- **The Deletion Plan is not a table.** It lives in graph state and is checkpointed with the paused graph ([03](03-graph-state.md)). Only the executed plan leaves a row, in `deletion_audit`.

## Prototype differences

The SQLite schema mirrors Postgres table for table, with these translations: `uuid` and `timestamptz` become `TEXT` (ISO 8601 UTC), `jsonb` and arrays become JSON `TEXT` with `json_valid` checks, `tsvector` becomes FTS5, `vector` becomes a float32 `BLOB` compared by cosine in Python. A test asserts both files declare the same tables, except:

- `interactions`: the prototype writes JSONL traces instead ([08](../hld/08-observability.md#in-the-prototype)).

Not in either file yet: **Trio Candidates**. They stay off until the client answers Q1 ([12](../hld/12-assumptions-and-open-questions.md)), and their table arrives with that slice.
