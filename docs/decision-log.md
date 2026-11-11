# Decision log

Decisions that don't need an ADR but must be explained in the HLD. Hard trade-offs live in `docs/adr/`.

## Saved Reports & deletion (Req 3)

- A Saved Report is created **only when the User explicitly asks** ("create a report", "save this"). Casual Q&A is never saved.
- Each Saved Report stores `author_id`, `conversation_id` (= LangGraph `thread_id`, a UUID created by the Chat API) and `created_at`. "Delete reports from this conversation" is an exact filter, never an LLM guess.
- Matching "reports mentioning X": entities (Brands, categories, Customer IDs, products) are extracted at save time into an indexed column, with full-text search as the fallback. Matching only ever covers the requesting User's own reports.
- Confirmation flow:
  1. Build a Deletion Plan (the exact list, each row showing why it matched, e.g. `mentions: Nike, Adidas`).
  2. Pause with `interrupt()`.
  3. The User replies `yes`, `no`, or `yes except 2,3`. In production these are checkboxes in Slack, Teams and web.
  4. Only the confirmed IDs are deleted.
  5. Any other message cancels and is answered normally.
  6. Zero matches means no confirmation is shown.
- Hard delete and an audit row happen in one transaction. The audit row is metadata only: who, when, count, IDs and titles, never content.

## Models (Req 5 cost, Req 8 agility)

- Tiered Gemini models: Flash-Lite for the router, guard and entity extraction; Flash for the analysis agent; Pro allowed for long report generation. The prototype defaults to Flash to stay inside free-tier limits.
- Model IDs and provider settings come from **environment files** (`.env`), not code.
- **One model setting per step of the path:** `MODEL_ROUTER`, `MODEL_ANALYST`, `MODEL_REPORT`, `MODEL_EXTRACTOR`, `MODEL_JUDGE`, each in `provider:model` form (e.g. `google_genai:gemini-…`), resolved through one model factory. Swapping a step's model or provider is a one-line `.env` change. Gemini is the default, and other providers stay supported.
- **HLD: model and provider experimentation.** A candidate model or provider is tested by running the offline eval suites per step and comparing scores, cost and latency against the current one in the observability tool's experiments view. It is then rolled out by shadow traffic → canary percentage → full, controlled by config, not redeploys.

## SQL self-correction (Req 5)

1. **Dry run first.** A BigQuery dry run is free, catches syntax and schema errors, and returns bytes to be scanned. Anything over the bytes cap is rejected before any cost.
2. **Error → retry.** The error message and the relevant schema go back to the model. At most 2 retries.
3. **Empty result → diagnose once.** One diagnostic step (e.g. check the distinct values of the filtered column: "California" vs "CA"), then one retry.
4. **Budget exhausted → graceful answer.** Explain what was tried and suggest a rephrasing. Never an exception in the UI.

The retry counter lives in graph state, so the cost per question is bounded and visible in traces.

## Golden Bucket (Req 1)

- **Prototype:** a small seed set of 5–10 hand-written Trios with simple embedding search. This is a stretch goal, cut first if time runs short.
- **Ingestion:** expert Trios land in object storage (GCS) → Cloud Run job → parse, strip figures (ADR 0005), tag Brands, embed → pgvector. Versioned; runs on new files and nightly.
- **Growth, with a human gate:**
  1. Saved Reports with strong signals (thumbs-up, no follow-up corrections) become anonymized Trio Candidates.
  2. A human analyst approves each Candidate before it is published.
  3. A nightly dry run of every Trio's SQL catches schema drift and retires broken ones.
- **Query time:** hybrid search (vector + keyword) over Trios the User may see, top-k injected into the analysis sub-agent through the `search_golden_trios` tool.

## Learning loop (Req 4)

- **User level:** a structured User Profile. Explicit statements ("always give me tables") apply immediately. "This time" is a one-off. A pattern repeated about 3 times is inferred by a background job, with a confidence threshold. The profile has three parts:
  1. **Core fields**: format, depth, charts, language. Typed.
  2. **Extensible attributes**: JSONB; new keys need no migration.
  3. **Notes**: a capped list (~20) of short free-text preferences. Length-limited, passed through the input guard, visible to and deletable by the User, injected as data, never as instructions.
  - Aggregation promotes recurring note types into attributes or core fields.
  - **Prototype:** notes only, in SQLite, if time allows (after Trios).
- **System level: humans in the loop, never self-rewriting in production.**
  - Every flow (trace, SQL, retries, feedback, rephrasings) is stored.
  - Periodic aggregation jobs cluster questions and failures to find gaps, e.g. new topics, recurring SQL errors, low-satisfaction areas.
  - A weekly review turns findings into new eval cases, Trio Candidates and prompt-change proposals.
  - Scheduled **domain-expert sessions** (analysts and business owners) review the aggregates and add knowledge the logs can't reveal.
  - Every change ships only if the eval set passes.

## Configuration vs. Persona (Req 8)

- **`.env`** holds deployment and engineering config: model IDs and tiers, provider, limits. Developers change it, and a restart is acceptable.
- **Persona** holds tone, report structure and emphasis. It is **not** environment config:
  - It is stored versioned (Postgres or a prompt registry such as Langfuse or LangSmith Prompt Hub).
  - Non-developers edit it in an admin UI with preview and rollback.
  - The agent loads it with a ~60 s cache, so there is no redeploy.
- Locked prompt layers (safety rules, tool contracts) cannot be edited through the Persona.
- **Prototype:** `persona.yaml`, re-read on every turn.

## Observability (Req 7)

- **Prototype:**
  - Always-on JSONL traces in `./traces/`, one span per node and tool call (tokens, latency, SQL, retries, guard decisions).
  - The `/trace` CLI command prints the last turn's step tree.
  - **Optional Arize Phoenix:** instrumentation is OpenTelemetry/OpenInference. Traces go to Phoenix when it is running (`uvx arize-phoenix serve`, or a single container in compose). `make eval` pushes results to Phoenix experiments. The README includes screenshots of a trace and an eval run.
  - **Why Phoenix for the prototype:** it is picked **for the demo and reviewer experience**. It runs as one process or container on SQLite, with no sign-up, so it ships anywhere, and it covers tracing, datasets, experiments and evals. Langfuse was the first candidate, but its self-hosted v3 needs 6 containers (ClickHouse, Redis, MinIO, …), which is too heavy to ask a reviewer to run.
  - **Production:** because instrumentation is vendor-neutral OTel, the backend is a deployment choice, not a code change. Options are Phoenix, or Langfuse (stronger prompt management and user analytics), plus Cloud Trace/Monitoring for infra.
- **HLD:** OpenTelemetry → a self-hosted LLM-trace backend (Phoenix or Langfuse; self-hosted because traces may hold business data), plus Cloud Monitoring and Logging for infra metrics and alerts.
- **Metrics:**
  - **Quality:** task success, first-try SQL success, self-correction success, empty-result rate, rephrase rate.
  - **Safety:** guard blocks by category; PII output-filter hits (target 0, alert on any); cross-Brand rewrites; delete confirm/cancel ratio.
  - **Cost:** tokens and $ per question, BigQuery bytes per question, retries per question.
  - **Reliability:** p50/p95 latency per node; LLM and BigQuery error rates; fallback activations; circuit-breaker state.
  - **North star:** trusted-answer rate (answered with no error, no rephrase, no negative feedback).
  - **Cost per *successful* answer**, not per question.
  - Every metric is broken down by intent and by node.
  - **SLOs with alerts:** p95 latency < 20 s; trusted-answer rate above target; PII-filter hits = 0.
  - **Synthetic canaries:** 3–5 known questions run end to end every 15 min.
  - **Online evals:** an LLM judge scores ~5% of production turns.
  - **Retrieval health:** the share of turns with a Golden Trio above the similarity threshold.
  - The prototype computes the per-turn data; north star, SLOs, canaries and online evals are HLD-only.
- **Interaction store (learning loop):**
  - Every turn is stored: question, plan, SQL, result *shape* (not rows), answer, feedback, trace.
  - PII is redacted on write.
  - Raw traces are kept 90 days; aggregated, anonymized data indefinitely.
  - Raw access is limited to the AI-ops team, and every read is logged.
  - A weekly job clusters questions and failures.

## Third-party resilience (Req 5)

- Timeouts on every external call.
- Exponential backoff with jitter (`tenacity`), for retryable errors only (429, 5xx, timeouts), within a per-turn budget.
- Model fallback Flash → Flash-Lite. The HLD adds a secondary provider or region.
- A circuit breaker per dependency.
- Client-side rate limiting matched to Gemini free-tier limits.
- The CLI loop never dies.
- **User-facing messages are non-technical:** no service names, error codes or stack traces. The technical detail goes to the trace, and the message carries a short reference ID for support.
- **Message catalog:** one plain-language message per failure class (data source down, model overloaded, self-correction exhausted, out of Brand scope, PII request). Every message offers a next step. The catalog lives in the Persona layer.
- **Unknown failures:**
  - A catch-all at the graph boundary maps any unclassified error to a generic "Something went wrong on my side. I've logged it (ref: `x`). Try rephrasing or ask again shortly."
  - The full error is traced and tagged `unclassified`, and any occurrence is alerted.
  - The weekly aggregation reviews unclassified errors, and recurring ones get their own class and message.
  - The Conversation state is preserved, so the User can simply continue.

## Quality assurance (Req 6)

- **HLD, three suites:**
  1. **Correctness:** execution match against reference SQL, plus an LLM judge with a rubric for intent.
  2. **Safety red team:** the gate is a 100% pass rate.
  3. **UX:** executive pilot sessions plus online signals (CSAT, turns to answer, rephrase rate, abandonment).
- **Prototype:**
  - A `pytest` safety suite (query guard, scoping, PII filter, Deletion Plan logic).
  - `make eval`: ~12 questions (8 correctness from the spec categories, 4 safety), scored by execution match or an LLM judge. Prints a scorecard and pushes it to Phoenix if running. Rate-limited for the free tier.

## Packaging & delivery

- **Ship anywhere (requirement):**
  - The native path is `uv sync && uv run agent chat`.
  - A multi-arch (amd64/arm64) Docker image of the app, with Google credentials passed via a mounted ADC file or a service-account key path.
  - Compose adds the optional observability backend.
- **Demo identity:** `seed/users.yaml` holds one Executive (`ceo`) and two Managers with 2–3 high-revenue `thelook` Brands each. Log in with `agent chat --as <id>`. In production, identity comes from SSO through the Channel Gateway.
- **HLD layout:** `docs/hld/`, one numbered file per section (overview, architecture, Reqs 1–8, stack rationale, operations, assumptions). This log and `open-items.md` get folded in. Later: an interactive way to present it.
- **Diagrams:** one Excalidraw hero architecture diagram (PNG + source) plus Mermaid for the sequence, graph, delete state machine, ingestion and learning loop.
- **Order of work:**
  1. Sharpen the HLD, iterating.
  2. Write the **LLD**: data schemas and code interfaces.
  3. Turn the work into GitHub issues.
  4. Build with TDD.
  5. Write the README.

## Production concerns

- **Channels:** the HLD stays high-level. The Channel Gateway is a pluggable component where new connectors (Slack, Teams, web, …) are added as adapters. No per-channel protocol detail in the HLD.
- **Deployment (reference GCP):**
  - **Cloud Run services:** Channel Gateway, Chat API, Admin UI.
  - **Cloud Run jobs + Cloud Scheduler:** Trio ingestion, nightly Trio validation, weekly aggregation, synthetic canaries.
  - **Data:** Cloud SQL Postgres, GCS (Golden Bucket), BigQuery.
  - **Secrets and network:** Secret Manager; a private VPC connector; IAP/SSO in front of the web UI and Admin UI.
  - **Scaling:** stateless services; all state is in Postgres and the checkpointer. Each box is a container, so it is portable to any container host.
- **Cost controls:**
  - Per-User daily quotas (tokens, BigQuery bytes).
  - A global daily spend cap, with an alert at 80%.
  - `maximum_bytes_billed` on every query.
  - Result caching for the same question and scope within N minutes, plus BigQuery's own cache.
  - The prototype enforces only the per-query bytes cap.
- **Long Conversations:** the last N turns are kept verbatim plus a running summary of older turns. Large results never go into history; a compact result summary does, and the full result stays in the turn record.
