# 06 · Ports

Each port is a `Protocol` in [`src/agent/ports/`](../../src/agent/ports/), and its docstring is the full interface: invariants, error modes and the adapters planned for it. This page fills in the [README checklist](README.md#checklist-for-each-tool-or-port) per port, and holds the trace-attribute names.

The thirteen ports below include three that only the code needs: **`UserDirectory`**, the seam between the `load_context` node and the identity tables; **`ScorecardSink`**, where eval runs go ([08](08-eval.md#phoenix)); and **`Delivery`**, declared but not wired ([ADR 0009](../adr/0009-outbound-actions-are-a-deliver-branch.md)). [ADR 0006](../adr/0006-ports-and-adapters.md) points here for the list.

| Port | Methods | Prototype adapter | Production adapter |
|---|---|---|---|
| [`LLMFactory`](../../src/agent/ports/llm.py) | `chat(step)`, `embeddings()` | `langchain-google-genai` on AI Studio | Vertex AI; second region, then second provider |
| [`QueryGuard`](../../src/agent/ports/query.py) | `check(sql, scope, row_limit)` | `sqlglot`, BigQuery dialect | same |
| [`QueryRunner`](../../src/agent/ports/query.py) | `dry_run(...)`, `execute(...)` | BigQuery, `bigquery-public-data` | BigQuery, governed copy; optionally as the User |
| [`PiiFilter`](../../src/agent/ports/pii.py) | `scan(text, templated)` | regex | Sensitive Data Protection, regex fallback |
| [`UserDirectory`](../../src/agent/ports/users.py) | `load(user_id)` | `seed/users.yaml` → SQLite (+ `list_dev_users()` for the web picker) | Postgres, kept in step by the admin sync |
| [`ReportRepository`](../../src/agent/ports/reports.py) | `save`, `find`, `get`, `match`, `delete_with_audit` | SQLite + FTS5 | Postgres + `tsvector` |
| [`TrioRetriever`](../../src/agent/ports/trios.py) | `search(query, scope, limit)` | SQLite + cosine over the seed Trios; Null when off | pgvector + full text, RRF |
| [`ProfileStore`](../../src/agent/ports/profile.py) | `load`, `save_standing`, `remove` | SQLite notes (stretch) | Postgres |
| [`PersonaStore`](../../src/agent/ports/persona.py) | `active`, `history`, `publish`, `record_rejection`, `rollback`, `events` | SQLite, same file as the users, `persona.yaml` as version 1 | Postgres, ~60 s cache; or a prompt registry |
| [`ChannelAdapter`](../../src/agent/ports/channel.py) | `deliver(message)`, per turn | CLI stdout; web SSE stream (optional) | Slack, Teams, web in the Gateway |
| [`Tracer`](../../src/agent/ports/tracer.py) | `turn(...)`, `span(name, ...)`, `current_trace_id()`, `read(since)` | JSONL; OTel → Phoenix if running | OTel Collector → Phoenix, Langfuse or LangSmith |
| [`ScorecardSink`](../../src/agent/ports/scorecards.py) | `publish(cases, scorecard)`, `baseline(...)` | JSON in `evals/runs/`; Phoenix experiments if running | same, or Langfuse / LangSmith datasets |
| [`Delivery`](../../src/agent/ports/delivery.py) | `render(report)`, `send(author_id, rendered, key)` | not wired | email, Slack, push |

In `Ports` ([composition.py](../../src/agent/composition.py)), `profiles` is `SqliteProfileStore` (#13), in the same file as the users; `None` (most tests) means an empty profile for every User and nothing saved. `reports` is the SQLite store, in the same file as the users (a report's Author is a foreign key into `users`). `trios` is `SqliteTrioRetriever` over the seed Trios (slice 7), in the same file again; `NullTrioRetriever` ([adapters/null.py](../../src/agent/adapters/null.py)) stands in when `MODEL_EMBEDDING` is empty and for `agent eval --no-trios`. `personas` is `SqlitePersonaStore` (#12), in the same file again; it reads its seed and cold-start default through `FilePersonaStore`, which serves `seed/persona.yaml` as version 1 only (catalog classes the Persona does not word use the bundled wording in [core/catalog.py](../../src/agent/core/catalog.py)).

## Checklist per port

### `LLMFactory`

- **Schema:** `ModelStep` enum, **one step per separate model call**: `router`, `deletion_filter`, `analyst`, `report`, `summarizer`, `entity_extraction`, `persona_drafter`, `persona_gate`, `trio_ingest`, `preference_inference`, `trio_candidate`, `judge`; returns LangChain `BaseChatModel` / `Embeddings`. Each step has `MODEL_<STEP>` and `MODEL_<STEP>_FALLBACK` in `ModelSettings` ([config.py](../../src/agent/config.py)); an unset value inherits per `INHERITS_FROM` ([ports/llm.py](../../src/agent/ports/llm.py)). Only `router` and `analyst` are required. Adding a model call means adding a step: a test fails if a step has no setting pair.
- **Depends on:** nothing else; it is the bottom of every model call.
- **Safety:** none of its own. Safety steps that use it (guard, router) **fail closed** when it raises.
- **Failures:** retries 429, 5xx and timeouts (≤ 3, `Retry-After`), then the step's fallback model, then `TransientError(MODEL_BUSY)`. A 400 or a permission error is never retried.
- **How it is built** (slice 3, [`llm_resilient.py`](../../src/agent/adapters/llm_resilient.py), [`resilience.py`](../../src/agent/adapters/resilience.py)): `chat(step)` returns a `ResilientChatModel` over the step's chain (primary, then fallback; a fallback equal to the primary is dropped). For each model in turn:
  1. **Circuit breaker**, one per *model*, shared by every step that uses it: open after `breaker_failures` (5) retryable failures within `breaker_window_s` (30 s); fail fast for `breaker_cooldown_s`; then one probe call; any answer from it but a retryable failure (a 400 included: the model is up) closes the breaker. Per model, not per provider, so a Flash outage leaves Flash-Lite, the fallback, callable.
  2. **Token bucket**, one per model: `llm_requests_per_minute` (10, the AI Studio free tier for Flash) with `llm_burst` (2). A call waits for its token; a wait over `llm_max_queue_s` (20 s) skips the model instead of hanging.
  3. **tenacity**, up to `retry_attempts` (3) tries on retryable errors only: exponential backoff with jitter, capped at `retry_max_wait_s` (10 s). The provider's delay replaces the backoff (an HTTP `Retry-After`, or the `RetryInfo.retryDelay` in Gemini's 429 body); a delay longer than the cap stops the retries and moves to the fallback.

  Retryable is LangChain's own tag (`ModelError.is_retryable`: 429, 5xx, connection, timeout), plus raw `httpx` transport errors and SDK `ServerError`. A known non-retryable error (400, 401, 403, 404) raises `PermanentError` at once, **without fallback**: another model would get the same bad request. Anything else raises an unclassified `AgentError`. `bind_tools` and `with_structured_output` are applied to every model in the chain, so a bound or structured call keeps the same fallback.
- **Trace:** an `llm` span per call: `llm.step`, `llm.model` (None when every model failed), `llm.fallback_used`, `llm.attempts`, `llm.errors` (`model: ErrorType` per model that failed, so a `MODEL_BUSY` turn says why), `breaker.state`, `llm.tokens.prompt`, `llm.tokens.completion`, `llm.latency_ms`. Not on the span: `llm.provider` (one provider in the prototype) and the cost, which `/stats` works out from the tokens and `MODEL_PRICES` (slice 5).
- **Tests** · slice 3 · `tests/test_resilience.py`: a 429 burst then success; `Retry-After` honoured; primary down → fallback; all down → `MODEL_BUSY`; a 400 is not retried and does not fall back; bound and structured calls fall back too; the breaker opens after 5 failures in 30 s, not when they are spread out, and closes after a successful probe; the rate limiter waits, and a wait over the queue limit is `MODEL_BUSY`; an unknown exception never crosses the port.
- **Gemini adapter notes** (`langchain-google-genai` 4.x, learned in slice 1; none of this shows in config):
  - `retries` counts attempts: `1` is one try, and `0` silently means the SDK default of 5 retries.
  - Pydantic schemas with nested models emit `$defs`, which the adapter ignores unless structured output uses `with_structured_output(..., method="json_schema")` and tools are bound as `convert_to_openai_tool(...)` specs (they inline `$defs`).
  - The AI Studio free tier allows about 5 requests a minute on Flash models; one analysis turn makes 3–5 calls, so back-to-back live checks hit 429. Space them, or use a billed key.

### `QueryGuard`

- **Schema:** `GuardDecision = GuardAllowed(rewritten_sql, params, scope, tables, filters, rewritten) | GuardRejected(reason, detail, hint, out_of_scope_brands)` ([domain/query.py](../../src/agent/domain/query.py)).
- **Depends on:** nothing. Pure, which makes it the cheapest module to test exhaustively.
- **Safety:** the core guarantee of Req 2 in the prototype ([03](../hld/03-safety-and-pii.md#query-guard-the-core-guarantee)).
- **Failures:** never raises for bad SQL; fails closed to `GuardRejected(UNPARSEABLE)`.
- **How it checks** ([adapters/query_guard.py](../../src/agent/adapters/query_guard.py)), in this order:
  1. one `SELECT` or set operation (CTEs allowed), else `NOT_SINGLE_SELECT`;
  2. an allowlist of node types over the whole tree (ADR 0011), else `UNSUPPORTED_SYNTAX`. Functions sqlglot does not know (`EXTERNAL_QUERY`, `ML.*`, UDFs), session reads (`SESSION_USER`, `CURRENT_USER`, every `CURRENT_*` except the clock) and query parameters (`@x`, `@@x`) are rejected: `run_sql` binds none, so the model inlines a Trio's parameters;
  3. tables by name, qualified only with `thelook_ecommerce` / `bigquery-public-data`; a CTE may not shadow a warehouse table, so the scoped subqueries' own table names always mean the warehouse (`TABLE_NOT_ALLOWED`);
  4. columns by name, case ignored: the seven PII columns plus `city` and `user_geom` (`COLUMN_NOT_ALLOWED`), and `num_of_item` for a Manager (`ORDER_LEVEL_FIELD`, hint: `COUNT(*)` per `order_id` on `order_items`). Other unknown names are left to the dry run: the scoped subqueries project only the allowlist, so they cannot resolve;
  5. Brand literals compared with a `brand` column (`=`, `!=`, `IS [NOT] DISTINCT FROM`, `IN (...)`, `IN UNNEST([...])`, `'x' IN (brand)`, `LIKE`, `STARTS_WITH`, `REGEXP_CONTAINS`, also under `LOWER`/`UPPER`/`TRIM`/`CAST`), case ignored except for a regex: one that can select no Brand in scope is `BRAND_OUT_OF_SCOPE` with the literals; `run_sql` adds the scope note as the hint. A comparison it cannot read (a computed literal such as `CONCAT('Pu', 'ma')`, a CTE column of another name) is still scoped by the rewrite, so it can only come back empty;
  6. the rewrite: each warehouse table becomes `(SELECT <allowlist> FROM t [WHERE <HLD 03 filter>]) AS <alias>`, the filters built from constant templates over `@brands`; `params = {"brands": sorted(scope)}` for a Manager, `{}` for an Executive. Then the outer `LIMIT`.
- **Filters** (slice 3): `GuardAllowed.filters` lists each `column <op> literal` of the SQL as the model wrote it (before the scope rewrite), in source order, through `DATE()`, `LOWER()`, `CAST` and the like; a column is kept only when its table is certain ([05](05-tools.md#run_sql)).
- **Brand comparisons** (slice 3): for a Manager, `brand` may be compared only with a literal, a column or `NULL`. A comparison with an expression (`LOWER('NIKE')`, `CONCAT(...)`, `IN (SELECT ...)`) is `BRAND_OUT_OF_SCOPE`: the scoped rewrite would return no other Brand's rows anyway, but only the refusal tells the User why. Input to the empty-result probe ([05](05-tools.md#run_sql)).
- **Trace:** `guard.decision`, `guard.reason`, `guard.rewritten`, `guard.tables`, `guard.scope_brands` (count for Managers, `"all"` for Executives), `sql.written` and `sql.rewritten` (None when rejected; both through the PII filter), on the `query_guard` span that `run_sql` opens around the call (the guard itself is pure).
- **sqlglot notes** (30.x, learned in slice 2; none of this shows in config):
  - Type nodes as `exp.Expr`: pyright reports `exp.Expression` as a private import.
  - `Select.args` keys the FROM clause as `from_`, not `from`.
  - Deep nesting raises `RecursionError`, not a `SqlglotError`, so `check` catches every exception.
- **Tests** · slice 2 · `tests/test_query_guard.py`: the full safety suite (see `run_sql` in [05](05-tools.md#run_sql)). Gated at 100%.

### `QueryRunner`

- **Schema:** `DryRunResult(bytes_processed, over_cap)`, `QueryResult(columns, rows, row_count, truncated, bytes_billed)`.
- **Depends on:** nothing; called only by `run_sql` and its probe, with guarded SQL.
- **Safety:** read-only identity; `maximum_bytes_billed` on every job; job timeout.
- **Failures:** `SelfCorrectableError` for syntax, schema and runtime errors (`BadRequest`, first message cleaned of the dataset path; never retried); `TransientError(DATA_SOURCE_DOWN)` after retries and the breaker; `PermanentError(DATA_SOURCE_DOWN)` for a permission or not-found error (a setup problem, not retried).
- **Resilience** (slice 3): each whole call (submit and wait) goes through the same `Retry` and `CircuitBreaker` as the model factory ([`resilience.py`](../../src/agent/adapters/resilience.py)), one breaker for BigQuery. Retryable: 429, 5xx, `DeadlineExceeded`, timeouts, raw `requests`/`google.auth` transport errors, and the 403 `rateLimitExceeded` BigQuery uses for its own rate limits. The client's own retries are off (`retry=None`, `job_retry=None`): its default job retry waits up to 40 minutes.
- **Trace:** the adapter adds none in the prototype; `run_sql` records `dry_run.bytes`, `bytes_billed` and `row_count` on its span ([05](05-tools.md#run_sql)).
- **Tests** · slices 1, 3 · `tests/test_run_sql.py` with a fake runner (syntax error, runtime error, 0 rows, outage); `tests/test_resilience.py` with a fake client (backend error retried, down → `DATA_SOURCE_DOWN` then the breaker fails fast, `BadRequest` not retried, permission error permanent).

### `PiiFilter`

- **Schema:** `PiiScan(text, hits: [PiiType], detectors)`.
- **Depends on:** nothing. Called by `Outbound.emit` and `save_report` only.
- **Safety:** ADR 0003 layer 3. Never skipped; falls back to regex.
- **Failures:** never raises; a managed-detector outage is recorded as `detectors = "regex"`.
- **Regex detectors** ([adapters/pii_regex.py](../../src/agent/adapters/pii_regex.py)), applied in order email, coordinates, phone, street address, postal code; a hit becomes `[REDACTED:<type>]`. Tuned so an answer's figures are not hits: a bare 5-digit number (a Customer ID) is a postal code only after a label (`ZIP`, `postal code`), in ZIP+4 form, or in address form: a word, a comma and a US state code or name, with a code in that state's ZIP range (`Boise, ID 83702`, `Boise, Idaho 83702`, and `Lakewood, NJ 8701` since the warehouse drops leading zeros), so `Customer ID 48213` stays; a bare coordinate pair needs five decimals; a phone needs separators.
- **Trace:** `pii.hits` (types only), `pii.detectors`, `pii.fallback`.
- **Tests** · slice 2 · `tests/test_pii.py`: each detector; redaction format; every `OutboundMessage` kind passes `Outbound.emit`; a saved report is filtered before the write.

### `UserDirectory`

- **Schema:** returns `UserContext(user_id, role, permissions, brands, access_version)` or `None`.
- **Depends on:** nothing.
- **Safety:** the only source of role, permissions and Brands. Default deny for no role; unknown User → `NO_ROLE`.
- **Failures:** `TransientError(UNAVAILABLE)`: without identity, the turn fails closed.
- **Trace:** `user.id`, `user.role`, `user.access_version` (never a name).
- **Dev only:** the seeded adapter also has `list_dev_users() -> list[DevUser]` for the web prototype's picker ([07](07-web.md#identity)). It is not on the port: production never lists Users to a channel.
- **Tests** · slice 1 · `tests/test_identity.py`: seeded Users load; a Manager with no Brands has an empty scope; an unknown User is refused. Slice 8 · `tests/test_web.py`: `list_dev_users` reports each User's role and Brands.

### `ReportRepository`

- **Schema:** see the Protocol; records in [domain/reports.py](../../src/agent/domain/reports.py). `save(report, entities, *, channel)` also records the Conversation the first time (the `conversations` row the reports' foreign key needs). `delete_with_audit(author_id, plan_id, report_ids, titles, filter)` returns a `DeletionReceipt(audit, already_gone, replayed)`.
- **Matching** (`find` and `match` share it): `this_conversation` is the exact `conversation_id`; a text or topic needs every word in the full-text index (each word quoted, so FTS5 operators in User text are plain words); each entity matches on `normalize_entity` (case-folded, punctuation dropped, a small alias table: `CK` → `calvin klein`) in `report_entities`, or as a full-text phrase as the fallback; a list of entities matches a report that mentions **any** of them; `created` is an inclusive range on the UTC date. Criteria combine with AND. A text with no letter or digit matches nothing (it is never dropped, which would match everything), and `DeletionFilter` refuses such a topic or entity. Plan items are oldest first; the reason wording is `match_reason`, one for every adapter.
- **Depends on:** nothing.
- **Safety:** every method is restricted to `author_id`; `delete_with_audit` re-checks ownership in the transaction. `ReportListItem.scope` is excluded from serialization, so an inaccessible report never shows the Brands it covers.
- **Failures:** `TransientError(UNAVAILABLE)` for a locked or unreachable file; a broken constraint is an unclassified `AgentError` (a bug, not an outage). Idempotency comes from `UNIQUE` constraints ([02](02-data.md#decisions-made-in-the-ddl)); a replayed `plan_id` is read back before anything is deleted.
- **Trace:** set by the calling tool.
- **Tests** · slice 4 · `tests/test_reports.py`, `tests/test_deletion.py`, run against both the SQLite adapter and an in-memory fake, so the fake stays honest.

### `TrioRetriever`

- **Schema:** `TrioSearchResult(matches: [TrioMatch], dropped_below_threshold)`.
- **Depends on:** `LLMFactory.embeddings` inside the adapter.
- **Safety:** Brand filter as defense in depth; `active` only.
- **Failures:** `TransientError`; callers continue without Trios and flag the turn degraded.
- **How it is built** (slice 7, [`trios_sqlite.py`](../../src/agent/adapters/trios_sqlite.py)):
  1. On construction, each `seed/trios/*.yaml` is validated as a `Trio` and the table is made equal to the seed (in the prototype the seed is the whole Golden Bucket): an unchanged Trio is left alone, a changed one is rewritten (the cascade drops its old vectors), a new active version supersedes the old one, and a Trio whose file was deleted is deleted. No network call, so start-up never waits for the model.
  2. The first search embeds every Trio that has no vectors yet, question and paraphrases in one `embed_documents` call, into `trio_embeddings` (768 float32, little-endian). They stay in the file: a restart embeds only queries. Changing `MODEL_EMBEDDING` needs a fresh app DB, since vectors of two models are not comparable.
  3. A search embeds the query (`embed_query`), keeps `active` Trios with `scope.covers(trio.scope)` (an `all_brands` Trio only for an all-Brands scope; a Trio tagged with no Brands counts as untagged, so Executives only: an empty set is inside every scope and would otherwise default open), scores each Trio by its **best** cosine over its question and paraphrases (clamped to [0, 1]), drops those under `trio_min_score` (0.75), and returns the best `limit`. `dropped_below_threshold` counts the Trios in scope that scored under the threshold; out-of-scope Trios are not counted.
  4. Any failure (the embedding model, the app DB) raises `TransientError(dependency="trios")`.
- **Embeddings** (`LLMFactory.embeddings()`, slice 7): `GoogleGenerativeAIEmbeddings` with `output_dimensionality=768`, behind the same breaker, token bucket and retries as a chat step, with an `llm` span whose `llm.step` is `embedding`, and no fallback. The embeddings class wraps every SDK error in its own `GoogleGenerativeAIError`, so retryability is read from the error it wraps.
- **Trace:** `trios.refs`, `trios.scores`, `trios.dropped`, `trios.degraded`, on the span of the caller (`trio_search` inside `retrieve_trios`, or `search_golden_trios`; [05](05-tools.md#search_golden_trios)). The root span's `retrieval.degraded` is true when `retrieve_trios` found the retriever down.
- **Tests** · slices 1, 7 · `tests/test_trios.py`: Null adapter returns nothing and the graph is unchanged; each Manager gets only their own Trio, never an `all_brands` one; the CEO gets the three `all_brands` Trios; below-threshold dropped and counted; best paraphrase wins, once per Trio; the seed is embedded once; an embedding outage or a vector of the wrong size is `TransientError("trios")`, and a turn then answers with `retrieval.degraded`; a Trio tagged with no Brands reaches no Manager; a new seed version supersedes the old; a deleted seed file is no longer retrieved. `tests/test_resilience.py`: an embedding 429 is retried; the embedding model down is `MODEL_BUSY`.

### `ProfileStore`

- **Schema:** `UserProfile`, `PreferenceStatement` ([domain/profile.py](../../src/agent/domain/profile.py)).
- **Depends on:** nothing.
- **Safety:** notes pass the input guard before they are saved and are injected as data.
- **Failures:** off the critical path; a failure is traced and the turn continues. The adapter raises `TransientError(UNAVAILABLE)` for any read or write that fails; `load_context` then uses an empty profile and records the error on a `profile_load` span (opened only on failure), and `save_preferences` records it on its `profile_save` span.
- **How it is built** ([`adapters/profile_sqlite.py`](../../src/agent/adapters/profile_sqlite.py)): one `user_profiles` row per User. `save_standing` and the graph's `apply_preferences` share one pure merge, `merged` in [domain/profile.py](../../src/agent/domain/profile.py), so what is saved is what was applied: each statement becomes an `explicit` value (confidence 1) and overwrites the field; a value that is not one of the field's options is skipped; a note already present (case ignored) is not added twice; past `PROFILE_MAX_NOTES` (20) the oldest notes go.
- **In the graph:** `router` → `apply_preferences` (stored profile, then standing, then this-turn statements, so "this time, prose" beats "always tables" in the same message) → the action's branch, or `acknowledge` for `preference_only`. When the router found standing statements, `save_preferences` runs beside `apply_preferences` in the same LangGraph step, writes no state and ends. *Prototype difference:* LangGraph starts the next step only when both are done, so the answer waits for the write (milliseconds on SQLite); HLD 01's "adds no latency" needs the save handed to a background task or the Worker in production. Every value the router extracted goes through the input guard's rules (`screen`) and must be one of its field's options (`is_option`) before it is applied, saved or acknowledged; a preference-only turn with nothing left is refused as `INJECTION`. The analyst prompt carries the merged profile as JSON in a `<user_preferences>` block, after the Persona, as data.
- **Trace:** `profile.saved` (count) and `profile.fields` (sorted field names) on the `profile_save` span inside `save_preferences`; `profile.snapshot` on the root span.
- **Tests** · slice 7 · `tests/test_profile.py`: this-turn never saved; explicit overwrites inferred; notes cap; duplicate note; reset and remove one; store down → the turn answers and both spans hold the error; injection note neither saved nor applied; standing applies in a new Conversation and not for another User; `/profile`, `/profile clear [n]` with no model call.

### `PersonaStore`

- **Schema:** `PersonaVersion`, `Persona`, `PersonaGateResult`, `PersonaEvent` ([domain/persona.py](../../src/agent/domain/persona.py)). `events` (added in #12) is the audit the HLD asks for (who, when, which path, what happened), so a rejection is visible through the port, not only in the table.
- **Depends on:** nothing.
- **Safety:** `publish` refuses a non-passing gate result; versions are immutable; exactly one active (index).
- **Failures:** `active()` never raises (the last version it read, then the bundled default); `rollback` raises `LookupError` at version 1; any other read or write that fails raises `TransientError(UNAVAILABLE)`, and a publish is one transaction.
- **Rollback target** ([`adapters/persona_sqlite.py`](../../src/agent/adapters/persona_sqlite.py)): undo walks back along what was active, replayed from `persona_events`, not by version number. v1 → v2 → v3, undo → v2, publish v4, undo → v2 (not v3), undo → v1, undo → `LookupError`.
- **Trace:** `persona.version` on every turn's root span.
- **Tests** · slice 7 · `tests/test_persona.py`: publish moves the pointer; refused without a pass; rollback; cold-start default.

### `ChannelAdapter`

- **Schema:** `OutboundMessage` out ([contracts/channel.py](../../src/agent/contracts/channel.py)). Outbound only: inbound messages are pushed by the channel into `ChatService.handle(message, channel)`, which wraps this adapter in a per-turn `Outbound` ([07](07-web.md)). So it is per turn or per connection, not in the process-wide `Ports`.
- **Depends on:** nothing. Called only by `Outbound`.
- **Safety:** receives only filtered messages.
- **Failures:** `deliver` never raises to the graph.
- **Trace:** `channel`.
- **Tests** · slice 1 · `tests/test_cli.py`: a failing turn does not end the loop; slash commands call `Views` and make no model call. Slice 8 · `tests/test_web.py`: the SSE stream carries the same messages as the CLI for one turn; every row of the [07](07-web.md#feature-map) feature map calls the same method from both channels; a closed stream drops and traces, and the turn is still checkpointed.

### `Tracer`

- **Schema:** `Span.set(**attrs)`, `Span.record_error(error_class, exc)`; `read(since)` returns `SpanRecord`s ([domain/traces.py](../../src/agent/domain/traces.py)): `trace_id`, `span_id`, `parent_id`, `name`, `start`, `duration_ms`, `attributes`, `error`.
- **Depends on:** nothing.
- **Safety:** never receives rows or PII. An error keeps its class and exception type, never its text; callers put free text (SQL, a warehouse error, a report filter) through the PII filter before setting it.
- **Failures:** never blocks or fails a turn. A JSONL write error and any export error are swallowed; `read` skips unreadable lines and never raises.
- **How it is built** (slice 5, [tracer_jsonl.py](../../src/agent/adapters/tracer_jsonl.py), [tracer_phoenix.py](../../src/agent/adapters/tracer_phoenix.py)): one JSON line per finished span in `traces/<UTC date>.jsonl`. The current span is a `ContextVar`, so a tool's span is a child of the node it runs in. Every graph node runs inside a span named after its `Node` (`_traced` in [graph/build.py](../../src/agent/graph/build.py)); a pause is not an error. `ChatService` sets the root attributes after the turn from the checkpoint, so a failed turn still records who asked and what was routed. The optional second sink rebuilds each record as an OTel span with OpenInference attributes (`openinference.span.kind`, `session.id`, `llm.model_name`, `llm.token_count.*`, `tool.name`) and the same IDs, on a `BatchSpanProcessor` to Phoenix's OTLP endpoint; it is used only when `PHOENIX_ENDPOINT` answers at start-up.
- **Full content in Phoenix (developer machines only).** With `PHOENIX_FULL_CONTENT=true` and Phoenix running, OpenInference's LangChain instrumentation is switched on as well. It records every LangGraph node, model call and tool call with its whole input and output: prompts, the model's replies and tool calls, and each tool's arguments and result. While each of our spans runs it is made the current OpenTelemetry span, so those spans nest under our `turn` and a question stays one trace. Each analysis tool call runs as a `RunnableLambda` named after the tool, which is how it gets its own input and output.
  - *Why:* our spans hold structure and numbers but no text, so Phoenix's Input and Output panes were empty, and following a turn meant piecing it together from attributes. When debugging a wrong answer, the prompt, the model's reasoning and the tool results are what matter. The instrumentation gives that view with no code of ours to keep in step with LangChain.
  - *What it costs:* those spans hold **result rows and unfiltered text** (the User's question, Customer values in tool results), which breaks this port's rule and HLD 03. That is why it is a separate switch, off by default (`Settings.phoenix_full_content = False`), never set outside a developer's machine. The JSONL traces, `/trace` and `/stats` are unaffected and keep the rule. In production the same view would come through the OTel Collector's redaction pass (HLD 08), not this switch.
  - *What it looks like:* every node appears twice under the turn, once as our span (attributes: guard decisions, row counts, outcome) and once inside the `LangGraph` span (inputs and outputs). The two are not merged because ours are written from finished records and LangChain's are live OTel spans.
- **Checking an export:** Phoenix serves what it received at `GET http://127.0.0.1:6006/v1/projects/default/spans?limit=200` (JSON; a span's kind is `span_kind`, its attributes flattened under `attributes`). Use it to confirm a turn arrived before asking anyone to look in the UI.
- **Trace:** it is the trace.
- **Tests** · slice 5 · `tests/test_tracing.py`: one root span per turn with the attributes below; node spans under the root and tool spans under their node; no rows or PII in the files; `/trace` renders the step tree from JSONL, `/trace <id>` an earlier turn, never another User's; the catalog reference ID is the ID `/trace` accepts; an export failure or an unwritable folder does not fail the turn; the OpenInference mapping; `/stats` from hand-written spans; slash commands make no model call.

### `ScorecardSink`

- **Schema:** `EvalCase`, `Scorecard` ([contracts/eval.py](../../src/agent/contracts/eval.py)).
- **Depends on:** nothing.
- **Safety:** cases and scorecards hold no rows or PII; traces are linked by ID.
- **Adapters:** `JsonScorecardSink` ([adapters/eval_files.py](../../src/agent/adapters/eval_files.py)), one `<run_id>.json` per run in `evals/runs/`; `PhoenixScorecardSink` ([adapters/scorecard_phoenix.py](../../src/agent/adapters/scorecard_phoenix.py)), Phoenix's REST API (`/v1/datasets/upload`, `/v1/datasets/{id}/experiments`, `/v1/experiments/{id}/runs`, `/v1/experiment_evaluations`) with the standard library only. `FanOutSink` ([evals/sinks.py](../../src/agent/evals/sinks.py)) publishes to both.
- **Baseline:** by run ID; with no ID, the latest JSON run on the same fixture hash whose outcome was PASS and that ran every case in `covering` (this run's correctness and step cases), so a failed run never becomes the bar and a subset run never hides a full one. Phoenix answers no baseline.
- **Failures:** a Phoenix outage (down, or an HTTP error) is logged and listed in `FanOutSink.failures`; the JSON file is always written. `--baseline <id>` that does not exist → CANNOT_MEASURE; no baseline at all → correctness is reported, not gated (PREREGISTRATION rule 4).
- **Trace:** none of its own. Each Phoenix experiment run carries the trial's first trace ID (as the 32-hex OTel ID the tracer exported), so a case links to its step tree.
- **Tests** · slice 6 · `tests/test_eval_harness.py`: the JSON sink writes every run and returns the latest passing one; Phoenix down is reported and the JSON is still written · slice 6b: a later subset run does not hide the full baseline; step cases are appended to an existing Phoenix dataset with one evaluation per check.

## Trace attributes

The root span (`turn`) carries: `trace_id`, `conversation_id`, `user.id`, `user.role`, `channel`, `router.verdict`, `router.action`, `persona.version`, `profile.snapshot`, `retrieval.gap`, `retrieval.degraded`, `outcome`, `error_class`, and `message_class` (the catalog message the turn ended with, if any). `outcome` is `answered`, `confirmation` (paused at a Deletion Plan), `refused` (input guard, no role, out-of-scope Brand), `gave_up` (another catalog message) or `error`. `profile.snapshot` holds typed values and a note count, never note text. A step eval trial ([08](08-eval.md#per-step-evals)) opens its own `turn` with `user.id` and `eval.case` (the case ID) instead; it runs one model step, not the graph. Every graph node is a span named after its `Node`; node and tool spans add the attributes in [05](05-tools.md) and above. `Outbound.emit` opens an `outbound` span per message with `kind` and `pii.hits` (types only). The confirmation node opens a `deletion_reply` span with `plan_id`, `reply` (`confirm`, `confirm_except`, `decline`, `not_an_answer`) and `outcome` (`cancelled`, or None when the plan goes on to `execute_deletion`). `/stats` ([08](../hld/08-observability.md#in-the-prototype)) is computed from these names only, so they are a contract: renaming one is an interface change and updates this page.
