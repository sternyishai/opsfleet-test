# 10 · Stack rationale

> **Deliverable.** The reasoning for the chosen cloud services, LLM models and frameworks, and the author's level of experience with them.

Every choice below sits behind a port ([ADR 0006](../adr/0006-ports-and-adapters.md)), so each one can be changed with one adapter and one wiring change. The rationale explains why each is the right *default*, not why it is the only option.

## Cloud: GCP as the reference deployment

The data already lives in BigQuery, and the client tests with Gemini. Running the rest of the system next to them removes a cross-cloud hop for every query and every model call, and lets one identity system cover all of it.

| Need | GCP service | Why this one | Portable equivalent |
|---|---|---|---|
| Run the Gateway, Chat API, Admin UI | **Cloud Run** services | Containers with no cluster to operate; scales to zero for the Admin UI; CPU always allocated for the Chat API, which finishes turns after acknowledging the channel | Any container host: AWS ECS/Fargate, Azure Container Apps, Kubernetes |
| Background jobs | **Pub/Sub** push to a worker **Cloud Run service**; **Cloud Run jobs** on **Cloud Scheduler** for batch | Same container images as the services. Pub/Sub gives at-least-once delivery and dead-letter topics with no broker to run; it pushes to a service because a Cloud Run job cannot receive a push | SQS + EventBridge, Azure Service Bus, RabbitMQ + cron |
| Analytics data | **BigQuery** | Where the data is. Free dry runs, `maximum_bytes_billed`, row access policies and policy tags give the safety and cost layers native support ([03](03-safety-and-pii.md), [06](06-resilience.md)) | Snowflake, Redshift: a new `QueryRunner` adapter and the guard's SQL dialect setting |
| App state + vectors | **Cloud SQL for PostgreSQL** + pgvector | One transactional store for checkpoints, reports, audit, profiles, Persona and Trios ([ADR 0002](../adr/0002-postgres-for-app-state.md)) | RDS, Azure Database for PostgreSQL, AlloyDB, self-hosted |
| Golden Bucket landing zone | **Cloud Storage** | Versioned bucket; object-finalize events trigger ingestion | S3, Azure Blob |
| Models | **Vertex AI** (Gemini) | Same project, IAM and region as the data; regional endpoints for fallback; provisioned throughput when needed. Google AI Studio for the prototype. | Any provider the model factory supports |
| PII detection | **Sensitive Data Protection** | Managed detectors for the output filter, with regex detectors as fallback | Amazon Comprehend PII, Presidio |
| Identity | **Google SSO**, **IAP** | Users already have Google identities *(assumption, [12](12-assumptions-and-open-questions.md))*, so queries can run with the User's own credentials and row access policies apply to them | Any OIDC provider; scoping then rests on the query guard |
| Secrets, network | **Secret Manager**, private VPC connector | Standard; no secrets in images or `.env` in production | Vault, AWS Secrets Manager |
| Infra metrics, logs, alerts | **Cloud Monitoring and Logging** | Native for Cloud Run and Cloud SQL; holds the SLO dashboards and alert policies ([08](08-observability.md)) | Prometheus + Grafana, Datadog |

## Models: tiered Gemini, one setting per step

Each step of the path uses the cheapest model that does it well. The router and guard run on every turn, so they must be fast and cheap; analysis and long reports are where quality matters most.

**Every separate model call is its own step, with its own setting.** So each one can be given a different model, evaluated on its own set and given its own fallback, without touching the others. An unset step inherits from a parent step, so `.env` needs only `MODEL_ROUTER` and `MODEL_ANALYST` to run.

| Step | Setting | Default tier | Inherits from | Why |
|---|---|---|---|---|
| Input guard + intent router (one call) | `MODEL_ROUTER` | Flash-Lite | required | Runs on every turn; a short classification with a fixed prompt |
| Delete request → structured filter | `MODEL_DELETION_FILTER` | Flash-Lite | router | Short structured output from the User's own message ([04](04-destructive-ops.md)) |
| Analysis sub-agent | `MODEL_ANALYST` | Flash | required | Multi-step tool use and SQL writing; the best balance of quality, latency and cost |
| Long report generation | `MODEL_REPORT` | Pro allowed | analyst | Rare (about 100 reports a day) and worth the extra quality |
| Running summary of older turns | `MODEL_SUMMARIZER` | Flash-Lite | router | Short compression; keeps long Conversations cheap ([01](01-architecture.md#conversation-state)) |
| Saved Report entity extraction | `MODEL_ENTITY_EXTRACTION` | Flash-Lite | router | Structured output on every save |
| Persona drafting | `MODEL_PERSONA_DRAFTER` | Flash | analyst | Turns the CEO's words into structured fields ([09](09-persona-management.md)) |
| Persona gate, checks 3 and 4 | `MODEL_PERSONA_GATE` | same as the analysis model | analyst | Must test the model Users actually get, so it follows the answer-writing model unless set on purpose |
| Trio ingestion: method summary, results removed | `MODEL_TRIO_INGEST` | Flash | analyst | Telling a method parameter from a measured result is subtle; runs only when an analyst adds a Trio ([02](02-hybrid-intelligence.md)) |
| Preference inference (daily job) | `MODEL_PREFERENCE_INFERENCE` | Flash-Lite | router | Background pattern detection ([05](05-learning-loop.md)) |
| Trio Candidate generation | `MODEL_TRIO_CANDIDATE` | Flash | analyst | Off until Q1 ([12](12-assumptions-and-open-questions.md)) |
| Eval and online judge | `MODEL_JUDGE` | Pro | analyst | Stronger than the Flash analysis model; the same tier as Pro reports, which rely on its calibration against human labels ([07](07-quality-assurance.md)) |
| Trio and question embeddings | `MODEL_EMBEDDING` | Gemini embedding model | none | Same provider and factory as every other call ([02](02-hybrid-intelligence.md)); no fallback, because vectors from different models do not compare |

- **Why Gemini.** The client tests with Gemini, Gemini Flash is strong at SQL and tool use for its price, and on Vertex AI it runs in the same project and region as the data.
- **No lock-in.** Every setting has the form `provider:model` (for example `google_genai:gemini-…`), resolved by one model factory, so swapping a step's model or provider is a one-line `.env` change. Exact model versions are in `.env`, not in this document, because they change faster than the design.
- **Changing a model is an experiment, not an edit.** A candidate runs through that step's offline evals and is compared with the current model on score, cost and latency, then rolled out gradually ([07](07-quality-assurance.md), [11](11-operations.md#deployment)).
- **Fallbacks** are per step (`MODEL_<STEP>_FALLBACK`, inherited the same way as the model): Flash → Flash-Lite in the prototype; another region, then another provider in production ([06](06-resilience.md)).
- **Prototype:** Flash for analysis and Flash-Lite for the router, both on the Google AI Studio free tier, with a client-side rate limiter sized to its limits.

## Framework: LangGraph

LangGraph is chosen because the hardest requirements map directly onto its primitives ([ADR 0001](../adr/0001-langgraph-agent-framework.md)):

| Requirement | LangGraph primitive |
|---|---|
| Safety steps the model cannot skip (Req 2) | A graph with fixed edges around a ReAct sub-graph ([ADR 0004](../adr/0004-hybrid-agent-graph.md)) |
| Confirm before delete, without breaking UX (Req 3) | `interrupt()` and resume, with the paused state checkpointed |
| Bounded self-correction (Req 5) | Retry loops as explicit edges, counters in graph state |
| Deep-dive debugging (Req 7) | Per-node spans; replay from any checkpoint ([08](08-observability.md)) |
| Conversations that survive restarts | Checkpointer per `thread_id` (Postgres in production, SQLite in the prototype) |
| Gemini without lock-in | Model-agnostic chat-model interface |

**Alternatives considered.** Google ADK was the strongest runner-up (Gemini-native) but is younger, and human-in-the-loop resume is more manual. PydanticAI has excellent typed tools but less mature multi-step orchestration and no built-in durable interrupt. A hand-rolled loop would mean rebuilding checkpointing, interrupts and tracing.

**Tools are in-process, not MCP servers.** Swapping a data source is already a new adapter, and keeping tools in-process keeps the User's identity a state read instead of a cross-service token. MCP is a named growth path ([ADR 0007](../adr/0007-in-process-tools-mcp-as-growth-option.md)).

## Supporting libraries

| Library | Used for | Why |
|---|---|---|
| **sqlglot** | Query guard: parse, check and rewrite SQL as an AST | Rewritable syntax tree, official BigQuery dialect, pure Python. Lenient by design, so the BigQuery dry run validates after it ([ADR 0011](../adr/0011-query-guard-parses-with-sqlglot.md)) |
| **Pydantic** | Tool inputs and outputs, events, domain records | Typed contracts the model and the tests share; the LLD is written as Pydantic schemas |
| **tenacity** | Backoff with jitter on retryable errors | Small, well-known, declarative ([06](06-resilience.md)) |
| **OpenTelemetry + OpenInference** | Spans for every node and tool | Vendor-neutral; the backend is a deployment choice ([08](08-observability.md)) |
| **Arize Phoenix** | Prototype traces, datasets and experiments | One process on SQLite, no sign-up, so a reviewer can run it; Langfuse self-hosting needs about six containers |
| **pgvector** | Golden Trio vector search | Vectors in the same database as everything else, hybrid search in one query |
| **uv** | Dependency management and running | Fast, reproducible lock file; `uv sync && uv run agent chat` is the whole setup |
| **pytest** | Deterministic tests against fakes | The safety suite runs offline on every commit ([07](07-quality-assurance.md)) |

## Author's experience

The assignment asks for the author's level of experience with the chosen stack. Choices were made on the requirements, not on familiarity. Experience is stated because it affects delivery risk, and each gap is covered by tests or kept behind a port.

| Technology | Experience | Delivery risk and how it is covered |
|---|---|---|
| LangGraph | 2 years, in production at work and in side projects; none with ADK or PydanticAI | Low |
| Gemini / Vertex AI | 1.5 years, in production at work and in side projects | Low |
| GCP (BigQuery, Cloud Run, Pub/Sub, Cloud SQL) | 4 years in production at work, across several GCP service areas | Low |
| Postgres + pgvector | None | Low for the prototype, which uses SQLite behind the same ports; Postgres and pgvector are production-only. The Postgres DDL was checked by loading it into `pgvector/pgvector:pg17` ([LLD 02](../lld/02-data.md)), and each production adapter would run the same port tests as its SQLite twin, as the Saved Reports store and its fake already do ([02](02-hybrid-intelligence.md)) |
| sqlglot | Not used before | The guard's behavior is specified in [03](03-safety-and-pii.md) and pinned by the `pytest` safety suite, written before the guard (TDD), so any gap shows up as a failing test ([07](07-quality-assurance.md)) |
| Phoenix / Langfuse | Phoenix 1 year, in side projects; Langfuse not used | Low: Phoenix in the prototype; Langfuse is a production option only, behind OpenTelemetry ([08](08-observability.md)) |
