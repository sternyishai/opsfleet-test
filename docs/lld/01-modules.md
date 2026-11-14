# 01 · Modules and seams

How `src/agent/` is cut, and why each cut sits where it does. Terms (module, interface, seam, adapter, depth) are used as in the codebase-design vocabulary: a **deep** module puts a lot of behaviour behind a small interface, and the **interface** is everything a caller must know, including invariants and error modes, not only the signature.

## Package map

```mermaid
flowchart TB
    cli[cli] --> comp[composition<br/>the only place that names adapters]
    comp --> graph
    comp --> adapters
    graph[graph<br/>state, node map] --> tools[tools<br/>6 analysis tools, 5 branch functions]
    graph --> core[core<br/>Outbound, parse_reply, PersonaQualityGate]
    tools --> ports
    core --> ports
    graph --> ports
    adapters[adapters<br/>provider SDKs live only here] -. satisfy .-> ports
    ports[ports<br/>Protocols] --> domain
    tools --> contracts
    graph --> contracts
    contracts[contracts<br/>router, channel, tool I/O] --> domain
    domain[domain<br/>Pydantic records] --> errors[errors<br/>ErrorClass, MessageClass]
```

| Package | Holds | Depends on |
|---|---|---|
| [`errors`](../../src/agent/errors.py) | The four error classes and the message-catalog keys ([06](../hld/06-resilience.md)) | nothing |
| [`domain/`](../../src/agent/domain/) | Records: identity and scope, Saved Reports and Deletion Plans, Trios, User Profile, Persona and gate result, guard decisions and query results | `errors` |
| [`contracts/`](../../src/agent/contracts/) | `RouterDecision`, channel messages, every tool's input and output | `domain` |
| [`ports/`](../../src/agent/ports/) | One `Protocol` per external dependency | `domain`, `contracts`, `langchain_core` types |
| [`core/`](../../src/agent/core/) | Deep modules with no dependency of their own: `ChatService` (the inbound entry point), `Views` (model-free commands), `Outbound` (single exit), `parse_reply` (confirmation), `PersonaQualityGate` | `ports` |
| [`tools/`](../../src/agent/tools/) | The eleven tool functions and `TurnContext` | `ports`, `contracts` |
| [`graph/`](../../src/agent/graph/) | `AgentState`, the `Node` map, `build_graph` | all of the above |
| [`adapters/`](../../src/agent/adapters/) | Concrete adapters, added slice by slice | `ports`, provider SDKs |
| [`composition`](../../src/agent/composition.py) | `Ports` bundle and `build_ports(settings)` | everything |
| [`config`](../../src/agent/config.py) | `Settings`, `ModelSettings`, `Limits` from `.env` | `pydantic-settings` |
| [`db/`](../../src/agent/db/) | DDL and the ER diagram ([02](02-data.md)) | nothing |

**The dependency rule is enforced, not hoped for.** Ruff's `banned-api` rule rejects `google.cloud`, `google.genai` and `langchain_google_genai` outside `agent.adapters` ([pyproject.toml](../../pyproject.toml)), which is the review smell ADR 0006 names. The core depends only on `pydantic`, `langchain-core` and `langgraph`; adapter SDKs are added by the slice that needs them.

## Where the seams are, and why

A seam is only worth having if something varies across it. Every port below has two real adapters, a fake for tests and a production one at least; the table says what varies.

| Seam | Varies across it | Depth: what hides behind the small interface |
|---|---|---|
| `LLMFactory.chat(step)` | Gemini on AI Studio, Vertex AI, another provider; fakes | Timeout, backoff on retryable errors, rate limiter, circuit breaker, fallback chain, span attributes. Callers get a chat model and nothing else to learn. |
| `QueryGuard.check(sql, scope)` | `sqlglot` dialect (BigQuery today, Snowflake later); a permissive fake is **not** allowed in safety tests | Parsing, allowlist, Brand detection, scope rewrite, parameter binding, forced `LIMIT`. One method, pure. |
| `QueryRunner` | BigQuery, a fake warehouse that injects errors | Dry run, `maximum_bytes_billed`, job timeout, error cleaning, retry. |
| `PiiFilter.scan(text)` | regex (prototype), Sensitive Data Protection with regex fallback (production) | Detectors, fallback, redaction format. |
| `ReportRepository` | SQLite + FTS5, Postgres + `tsvector`, in-memory fake | Author restriction, idempotent save, entity and full-text matching, the delete-and-audit transaction. |
| `TrioRetriever.search` | Null (slice cut), SQLite + cosine, pgvector + RRF | Embedding, hybrid search, Brand filter, threshold. |
| `UserDirectory.load` | `seed/users.yaml` + SQLite, Postgres fed by the admin sync | Joining role, permissions and Brand Assignments; the access version. |
| `ProfileStore`, `PersonaStore` | SQLite, Postgres; a prompt registry for the Persona | Explicit-beats-inferred, notes cap; versioning, active pointer, cache and cold-start default. |
| `ChannelAdapter` | CLI, web SSE, Slack, Teams | Rendering per channel. Outbound only and per turn; channels push inbound messages into `ChatService` ([07](07-web.md)). |
| `Tracer` | JSONL, OpenTelemetry → Phoenix, Langfuse or LangSmith | Span export, batching, drop-on-overflow. |
| `Delivery` | email, Slack, push | **Not wired** ([ADR 0009](../adr/0009-outbound-actions-are-a-deliver-branch.md)). The Protocol is there so the seam is concrete when the first adapter arrives. |

**Not a port: the checkpointer.** LangGraph's `BaseCheckpointSaver` already is a seam with SQLite and Postgres adapters; wrapping it again would be a pass-through.

**Not a port: the event bus and the interaction store.** Pub/Sub and `interactions` are production-only. With one consumer and no prototype adapter, a Protocol now would be a hypothetical seam. They get one with the first background job.

## Deep modules that are not ports

These live in `core/` and `tools/`. They have one adapter each (themselves), so they are modules, not seams. Tests cross the same interface the graph uses.

- **`run_sql`**: the largest module. Guard, dry run, execute, the 2-retry budget, the empty-result probe and the graceful give-up are all behind one function `(RunSqlInput, TurnContext, SqlBudget) -> (RunSqlOutput, SqlBudget)`. The budget goes in and comes out, so the loop is testable without a graph.
- **`ChatService.handle`** and **`Views`**: every channel calls these two and nothing else, so the CLI and the web UI offer the same features with no logic of their own ([07](07-web.md)).
- **`Outbound.emit`**: the single exit. Deleting it would put a PII-filter call at every place that sends anything: the deletion test says it earns its keep.
- **`parse_reply`**: the confirmation grammar (`yes`, `no`, `yes except 2,3`, the large-plan count). Pure.
- **`PersonaQualityGate.check`**: five checks behind one method. Its fixtures (red-team cases, stored sample results) are internal seams for its own tests.

## Identity never crosses the model

Tool inputs carry what the model decides (SQL, a filter, a report ID). They never carry a User, an Author or a Brand scope. Those come from `TurnContext.user`, built by the graph from state ([05](05-tools.md)). A test in [tests/test_skeleton.py](../../tests/test_skeleton.py) fails if any `*Input` model grows a field named `user_id`, `author_id`, `brands`, `scope` or `role`.
