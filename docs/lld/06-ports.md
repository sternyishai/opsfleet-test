# 06 · Ports

Each port is a `Protocol` in [`src/agent/ports/`](../../src/agent/ports/), and its docstring is the full interface: invariants, error modes and the adapters planned for it. This page fills in the [README checklist](README.md#checklist-for-each-tool-or-port) per port, and holds the trace-attribute names.

ADR 0006 lists ten ports. The LLD adds three: **`UserDirectory`**, the seam between the `load_context` node and the identity tables; **`ScorecardSink`**, where eval runs go ([08](08-eval.md#phoenix)); and **`Delivery`**, declared but not wired ([ADR 0009](../adr/0009-outbound-actions-are-a-deliver-branch.md)).

| Port | Methods | Prototype adapter | Production adapter |
|---|---|---|---|
| [`LLMFactory`](../../src/agent/ports/llm.py) | `chat(step)`, `embeddings()` | `langchain-google-genai` on AI Studio | Vertex AI; second region, then second provider |
| [`QueryGuard`](../../src/agent/ports/query.py) | `check(sql, scope, row_limit)` | `sqlglot`, BigQuery dialect | same |
| [`QueryRunner`](../../src/agent/ports/query.py) | `dry_run(...)`, `execute(...)` | BigQuery, `bigquery-public-data` | BigQuery, governed copy; optionally as the User |
| [`PiiFilter`](../../src/agent/ports/pii.py) | `scan(text, templated)` | regex | Sensitive Data Protection, regex fallback |
| [`UserDirectory`](../../src/agent/ports/users.py) | `load(user_id)` | `seed/users.yaml` → SQLite | Postgres, kept in step by the admin sync |
| [`ReportRepository`](../../src/agent/ports/reports.py) | `save`, `find`, `get`, `match`, `delete_with_audit` | SQLite + FTS5 | Postgres + `tsvector` |
| [`TrioRetriever`](../../src/agent/ports/trios.py) | `search(query, scope, limit)` | Null; SQLite + cosine (stretch) | pgvector + full text, RRF |
| [`ProfileStore`](../../src/agent/ports/profile.py) | `load`, `save_standing`, `remove` | SQLite notes (stretch) | Postgres |
| [`PersonaStore`](../../src/agent/ports/persona.py) | `active`, `history`, `publish`, `record_rejection`, `rollback` | bundled `persona.yaml`; SQLite (stretch) | Postgres, ~60 s cache; or a prompt registry |
| [`ChannelAdapter`](../../src/agent/ports/channel.py) | `deliver(message)`, per turn | CLI stdout; web SSE stream (optional) | Slack, Teams, web in the Gateway |
| [`Tracer`](../../src/agent/ports/tracer.py) | `turn(...)`, `span(name, ...)`, `current_trace_id()` | JSONL; OTel → Phoenix if running | OTel Collector → Phoenix, Langfuse or LangSmith |
| [`ScorecardSink`](../../src/agent/ports/scorecards.py) | `publish(cases, scorecard)`, `baseline(...)` | JSON in `evals/runs/`; Phoenix experiments if running | same, or Langfuse / LangSmith datasets |
| [`Delivery`](../../src/agent/ports/delivery.py) | `render(report)`, `send(author_id, rendered, key)` | not wired | email, Slack, push |

In `Ports` ([composition.py](../../src/agent/composition.py)), `reports` and `profiles` are `None` until slices 4 and 7 wire them; until then every User has an empty profile. Slice 1 stand-ins: `NullTrioRetriever` and `PassThroughPiiFilter` ([adapters/null.py](../../src/agent/adapters/null.py)), and `FilePersonaStore` serving `seed/persona.yaml` as version 1 (catalog classes it does not word use the bundled wording in [core/catalog.py](../../src/agent/core/catalog.py)).

## Checklist per port

### `LLMFactory`

- **Schema:** `ModelStep` enum, **one step per separate model call**: `router`, `deletion_filter`, `analyst`, `report`, `summarizer`, `entity_extraction`, `persona_drafter`, `persona_gate`, `trio_ingest`, `preference_inference`, `trio_candidate`, `judge`; returns LangChain `BaseChatModel` / `Embeddings`. Each step has `MODEL_<STEP>` and `MODEL_<STEP>_FALLBACK` in `ModelSettings` ([config.py](../../src/agent/config.py)); an unset value inherits per `INHERITS_FROM` ([ports/llm.py](../../src/agent/ports/llm.py)). Only `router` and `analyst` are required. Adding a model call means adding a step: a test fails if a step has no setting pair.
- **Depends on:** nothing else; it is the bottom of every model call.
- **Safety:** none of its own. Safety steps that use it (guard, router) **fail closed** when it raises.
- **Failures:** retries 429, 5xx and timeouts (≤ 3, `Retry-After`), then the step's fallback model, then `TransientError(MODEL_BUSY)`. A 400 or a permission error is never retried.
- **Trace:** `llm.step`, `llm.model`, `llm.provider`, `llm.fallback_used`, `llm.tokens.prompt`, `llm.tokens.completion`, `llm.cost_usd`, `llm.latency_ms`, `breaker.state`.
- **Tests** · slice 3 · `tests/test_resilience.py`: a 429 burst then success; primary down → fallback; all down → `MODEL_BUSY`; a 400 is not retried; the breaker opens after 5 failures in 30 s.

### `QueryGuard`

- **Schema:** `GuardDecision = GuardAllowed(rewritten_sql, params, scope, tables, filters, rewritten) | GuardRejected(reason, detail, hint, out_of_scope_brands)` ([domain/query.py](../../src/agent/domain/query.py)).
- **Depends on:** nothing. Pure, which makes it the cheapest module to test exhaustively.
- **Safety:** the core guarantee of Req 2 in the prototype ([03](../hld/03-safety-and-pii.md#query-guard-the-core-guarantee)).
- **Failures:** never raises for bad SQL; fails closed to `GuardRejected(UNPARSEABLE)`.
- **Trace:** `guard.decision`, `guard.reason`, `guard.rewritten`, `guard.tables`, `guard.scope_brands` (count for Managers).
- **Tests** · slice 2 · `tests/test_query_guard.py`: the full safety suite (see `run_sql` in [05](05-tools.md#run_sql)). Gated at 100%.

### `QueryRunner`

- **Schema:** `DryRunResult(bytes_processed, over_cap)`, `QueryResult(columns, rows, row_count, truncated, bytes_billed)`.
- **Depends on:** nothing; called only by `run_sql` and its probe, with guarded SQL.
- **Safety:** read-only identity; `maximum_bytes_billed` on every job; job timeout.
- **Failures:** `SelfCorrectableError` for syntax, schema and runtime errors; `TransientError(DATA_SOURCE_DOWN)` after retries and the breaker.
- **Trace:** `bq.dry_run.bytes`, `bq.bytes_billed`, `bq.job_id`, `bq.row_count`, `bq.latency_ms`.
- **Tests** · slices 1, 3 · `tests/test_run_sql.py` with a fake runner: syntax error, runtime error, 0 rows, outage.

### `PiiFilter`

- **Schema:** `PiiScan(text, hits: [PiiType], detectors)`.
- **Depends on:** nothing. Called by `Outbound.emit` and `save_report` only.
- **Safety:** ADR 0003 layer 3. Never skipped; falls back to regex.
- **Failures:** never raises; a managed-detector outage is recorded as `detectors = "regex"`.
- **Trace:** `pii.hits` (types only), `pii.detectors`, `pii.fallback`.
- **Tests** · slice 2 · `tests/test_pii.py`: each detector; redaction format; every `OutboundMessage` kind passes `Outbound.emit`; a saved report is filtered before the write.

### `UserDirectory`

- **Schema:** returns `UserContext(user_id, role, permissions, brands, access_version)` or `None`.
- **Depends on:** nothing.
- **Safety:** the only source of role, permissions and Brands. Default deny for no role; unknown User → `NO_ROLE`.
- **Failures:** `TransientError(UNAVAILABLE)`: without identity, the turn fails closed.
- **Trace:** `user.id`, `user.role`, `user.access_version` (never a name).
- **Tests** · slice 1 · `tests/test_identity.py`: seeded Users load; a Manager with no Brands has an empty scope; an unknown User is refused.

### `ReportRepository`

- **Schema:** see the Protocol; records in [domain/reports.py](../../src/agent/domain/reports.py).
- **Depends on:** nothing.
- **Safety:** every method is restricted to `author_id`; `delete_with_audit` re-checks ownership in the transaction.
- **Failures:** `TransientError(UNAVAILABLE)`. Idempotency comes from `UNIQUE` constraints ([02](02-data.md#decisions-made-in-the-ddl)).
- **Trace:** set by the calling tool.
- **Tests** · slice 4 · `tests/test_reports.py`, `tests/test_deletion.py`, run against both the SQLite adapter and an in-memory fake, so the fake stays honest.

### `TrioRetriever`

- **Schema:** `TrioSearchResult(matches: [TrioMatch], dropped_below_threshold)`.
- **Depends on:** `LLMFactory.embeddings` inside the adapter.
- **Safety:** Brand filter as defense in depth; `active` only.
- **Failures:** `TransientError`; callers continue without Trios and flag the turn degraded.
- **Trace:** `trios.refs`, `trios.scores`, `trios.dropped`.
- **Tests** · slices 1, 7 · `tests/test_trios.py`: Null adapter returns nothing and the graph is unchanged; scope filter; threshold.

### `ProfileStore`

- **Schema:** `UserProfile`, `PreferenceStatement` ([domain/profile.py](../../src/agent/domain/profile.py)).
- **Depends on:** nothing.
- **Safety:** notes pass the input guard before they are saved and are injected as data.
- **Failures:** off the critical path; a failure is traced and the turn continues.
- **Trace:** `profile.saved` (count), `profile.fields`.
- **Tests** · slice 7 · `tests/test_profile.py`: this-turn never saved; explicit overwrites; notes cap; reset.

### `PersonaStore`

- **Schema:** `PersonaVersion`, `Persona`, `PersonaGateResult` ([domain/persona.py](../../src/agent/domain/persona.py)).
- **Depends on:** nothing.
- **Safety:** `publish` refuses a non-passing gate result; versions are immutable; exactly one active (index).
- **Failures:** `active()` never raises (cache, then bundled default); `rollback` raises `LookupError` at version 1.
- **Trace:** `persona.version` on every turn's root span.
- **Tests** · slice 7 · `tests/test_persona.py`: publish moves the pointer; refused without a pass; rollback; cold-start default.

### `ChannelAdapter`

- **Schema:** `OutboundMessage` out ([contracts/channel.py](../../src/agent/contracts/channel.py)). Outbound only: inbound messages are pushed by the channel into `ChatService.handle(message, channel)`, which wraps this adapter in a per-turn `Outbound` ([07](07-web.md)). So it is per turn or per connection, not in the process-wide `Ports`.
- **Depends on:** nothing. Called only by `Outbound`.
- **Safety:** receives only filtered messages.
- **Failures:** `deliver` never raises to the graph.
- **Trace:** `channel`.
- **Tests** · slice 1 · `tests/test_cli.py`: a failing turn does not end the loop; slash commands call `Views` and make no model call. Slice 8 · `tests/test_web.py`: the SSE stream carries the same messages as the CLI for one turn; every row of the [07](07-web.md#feature-map) feature map calls the same method from both channels.

### `Tracer`

- **Schema:** `Span.set(**attrs)`, `Span.record_error(error_class, exc)`.
- **Depends on:** nothing.
- **Safety:** never receives rows or PII.
- **Failures:** never blocks or fails a turn.
- **Trace:** it is the trace.
- **Tests** · slice 5 · `tests/test_tracing.py`: one root span per turn with the attributes below; `/trace` renders the step tree from JSONL; an export failure does not fail the turn.

### `ScorecardSink`

- **Schema:** `EvalCase`, `Scorecard` ([contracts/eval.py](../../src/agent/contracts/eval.py)).
- **Depends on:** nothing.
- **Safety:** cases and scorecards hold no rows or PII; traces are linked by ID.
- **Failures:** a Phoenix outage is reported; the JSON file is always written. No baseline → the regression gate is CANNOT_MEASURE.
- **Trace:** none of its own.
- **Tests** · slice 6 · `tests/test_eval_harness.py`: the negative control fails; a crash is CANNOT_MEASURE, not FAIL; a manifest mismatch refuses the comparison.

## Trace attributes

The root span (`turn`) carries: `trace_id`, `conversation_id`, `user.id`, `user.role`, `channel`, `router.verdict`, `router.action`, `persona.version`, `profile.snapshot`, `retrieval.gap`, `retrieval.degraded`, `outcome`, `error_class`. Node and tool spans add the attributes in [05](05-tools.md) and above. `/stats` ([08](../hld/08-observability.md#in-the-prototype)) is computed from these names only, so they are a contract: renaming one is an interface change and updates this page.
