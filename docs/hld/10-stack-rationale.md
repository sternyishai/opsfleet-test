# 10 · Stack rationale

> **Deliverable.** The reasoning for the chosen cloud services, LLM models and frameworks, and the author's level of experience with them.

Every choice below sits behind a port ([ADR 0006](../adr/0006-ports-and-adapters.md)), so each one can be changed with one adapter and one wiring change. The rationale explains why each is the right *default*, not why it is the only option.

## Cloud: GCP as the reference deployment

The data already lives in BigQuery, and the client tests with Gemini. Running the rest of the system next to them removes a cross-cloud hop for every query and every model call, and lets one identity system cover all of it.

| Need | GCP service | Why this one | Portable equivalent |
|---|---|---|---|
| Run the Gateway, Chat API, Admin UI | **Cloud Run** services | Containers with no cluster to operate; scales to zero for the Admin UI; CPU always allocated for the Chat API, which finishes turns after acknowledging the channel | Any container host: AWS ECS/Fargate, Azure Container Apps, Kubernetes |
| Background jobs | **Cloud Run jobs**, **Cloud Scheduler**, **Pub/Sub** | Same container images as the services; Pub/Sub gives at-least-once delivery and dead-letter topics with no broker to run | SQS + EventBridge, Azure Service Bus, RabbitMQ + cron |
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

| Step | Setting | Default tier | Why |
|---|---|---|---|
| Input guard + intent router | `MODEL_ROUTER` | Flash-Lite | Runs on every turn; a short classification with a fixed prompt |
| Analysis sub-agent | `MODEL_ANALYST` | Flash | Multi-step tool use and SQL writing; the best balance of quality, latency and cost |
| Long report generation | `MODEL_REPORT` | Pro allowed | Rare (about 100 reports a day) and worth the extra quality |
| Entity extraction, figure stripping | `MODEL_EXTRACTOR` | Flash-Lite | Background, structured output |
| Eval and online judge | `MODEL_JUDGE` | Pro | Must be stronger than the model it judges ([07](07-quality-assurance.md)) |
| Trio and question embeddings | `MODEL_EMBEDDING` | Gemini embedding model | Same provider and factory as every other call ([02](02-hybrid-intelligence.md)) |

- **Why Gemini.** The client tests with Gemini, Gemini Flash is strong at SQL and tool use for its price, and on Vertex AI it runs in the same project and region as the data.
- **No lock-in.** Every setting has the form `provider:model` (for example `google_genai:gemini-…`), resolved by one model factory, so swapping a step's model or provider is a one-line `.env` change. Exact model versions are in `.env`, not in this document, because they change faster than the design.
- **Changing a model is an experiment, not an edit.** A candidate runs through that step's offline evals and is compared with the current model on score, cost and latency, then rolled out through shadow traffic, a canary and full rollout by configuration ([07](07-quality-assurance.md)).
- **Fallbacks** are per step (`MODEL_ANALYST_FALLBACK`, …): Flash → Flash-Lite in the prototype; another region, then another provider in production ([06](06-resilience.md)).
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
| **sqlglot** | Query guard: parse, check and rewrite SQL as an AST | Real BigQuery-dialect parser, fails closed, and supports other dialects if the warehouse changes ([03](03-safety-and-pii.md)) |
| **Pydantic** | Tool inputs and outputs, events, domain records | Typed contracts the model and the tests share; the LLD is written as Pydantic schemas |
| **tenacity** | Backoff with jitter on retryable errors | Small, well-known, declarative ([06](06-resilience.md)) |
| **OpenTelemetry + OpenInference** | Spans for every node and tool | Vendor-neutral; the backend is a deployment choice ([08](08-observability.md)) |
| **Arize Phoenix** | Prototype traces, datasets and experiments | One process on SQLite, no sign-up, so a reviewer can run it; Langfuse self-hosting needs about six containers |
| **pgvector** | Golden Trio vector search | Vectors in the same database as everything else, hybrid search in one query |
| **uv** | Dependency management and running | Fast, reproducible lock file; `uv sync && uv run agent chat` is the whole setup |
| **pytest** | Deterministic tests against fakes | The safety suite runs offline on every commit ([07](07-quality-assurance.md)) |

## Author's experience

The assignment asks for the author's level of experience with the chosen stack. It is stated plainly here, because it affected the choices: where experience was lacking, the simpler or better-documented option was preferred.

| Technology | Experience | Effect on the design |
|---|---|---|
| LangGraph | Hands-on; none with ADK or PydanticAI | Lowered delivery risk for the hardest requirements (interrupts, checkpointing) |
| Gemini / Vertex AI | _to fill in_ | |
| BigQuery | _to fill in_ | |
| GCP (Cloud Run, Pub/Sub, Cloud SQL) | _to fill in_ | |
| Postgres + pgvector | _to fill in_ | |
| sqlglot | _to fill in_ | |
| Phoenix / Langfuse | _to fill in_ | |
