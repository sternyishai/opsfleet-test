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
- **Storage format:** one `saved_reports` row holds the exact Markdown the User saw, plus structured JSONB (title, summary, insights, action items, period). Structured fields keep action items queryable and let each channel render the report its own way. Future charts are files in object storage, referenced from the report.
- **Reuse:** the agent may search the requesting User's **own** Saved Reports ("compare with my report from last month"), using the same entity + full-text search as deletion. Saved Reports are never part of the shared Golden RAG.
- **Deletion vs. the Golden pipeline:** deleting a Saved Report also deletes any Trio Candidate derived from it that is still pending review (hard delete). A Trio an analyst has already approved and rewritten is company knowledge and stays.
- **Viewing own reports:** the analysis agent has two read-only tools, `list_my_reports(filter?)` and `get_my_report(id)`. Neither takes an author parameter; the Author is injected from the session by code, so a prompt cannot reach another User's reports. An opened report is labelled "data as of <saved date>". Channels also offer a direct, model-free view: `/reports` in the CLI (prototype), and a "My reports" view or command in web, Slack and Teams.
- **Access re-checked at use** (*added 2026-11-14*): each Saved Report records its `scope` (Brands, from the query guard at save time). Listing, opening and comparing check it against current Brand Assignments; a report with a Brand the User no longer has shows its title only and can still be deleted. Graph state records the Brand Assignment version; if it changed, earlier results and the running summary leave the prompt.
- **Deletion hardening** (detail in `docs/hld/04-destructive-ops.md`):
  - The model only turns the request into a structured filter (conversation, entities, date range, all); code runs it with `author_id` injected. The model never picks report IDs.
  - The Deletion Plan stores IDs, not the filter, so reports saved after the plan was shown are never deleted by it. Ownership is checked again in the DELETE itself.
  - Plans over 20 reports show the first 20 plus the total; deleting all needs the count ("yes, delete all 37"); a bare "yes" gets one re-prompt.
  - Plans have an ID and are marked executed: double clicks and replays are no-ops; buttons from old plans are rejected as expired. A confirmation counts only from the plan's User, in its Conversation, for the latest open plan; a new plan cancels the previous one (*added 2026-11-14*). Reports already deleted elsewhere are reported, not treated as errors.
  - A `report.deleted` event after commit cleans up related files (e.g. chart images).
  - Entity extraction is asynchronous in production (full-text covers the gap) and synchronous at save time in the prototype; the prototype uses SQLite FTS5.
  - Metrics: confirm/cancel ratio, partial-confirmation rate, zero-match rate, plan size; a rising cancel rate becomes eval cases.

## Intent routing

- **Action, search question and preferences are separate outputs of one router call.** The router (Flash-Lite) returns the turn's single *action* (`analysis`, `delete reports`, `change Persona`, `preference only`), for `analysis` a standalone *search question* used by `retrieve_trios` (ADR 0008), and a list of *preference statements*, each *standing* or *this turn*. Any message can carry both ("compare CA vs NY, and use tables from now on").
- **Preferences apply to the current turn immediately** (merged into the in-memory User Profile before any branch formats an answer). Standing preferences are persisted in a parallel branch, with no added latency; this-turn preferences are never persisted.
- **One action per turn; delete goes first.** A message that mixes a delete with another request runs the Deletion Plan first. The other request is kept in graph state as a *deferred request*, announced to the User, and run after the confirmation is answered (yes or no). It is never dropped silently and never run before the confirmation.
- **Any reply to a confirmation prompt that is not a clear answer** cancels the plan and re-enters the graph at the input guard, never past it.
- **Outbound actions are a `deliver` branch** ([ADR 0009](adr/0009-outbound-actions-are-a-deliver-branch.md)). Email, Slack posts and push notifications are adapters of one `Delivery` port, reached only from a router-opened branch, never tools of the analysis model, so injected text cannot make the system send data out.
  - `deliver` sends a Saved Report (an unsaved answer is saved first), never raw rows. The recipient is injected by code: the Author, until the client answers Q15. It confirms with `interrupt()` and sends through the single outbound function, so delivered content passes the output PII filter.
  - "…and email it to me" returns `analysis` as the action and `deliver` as a *follow-up action*: analysis first, then delivery, the reverse of a deferred request.
  - Read capabilities (charts, web search) stay analysis tools. If they grow past about 10–20, filter per role or turn first, then group into specialist sub-agents.
  - No model in the branch: load_report → render (a template per adapter) → `interrupt()` preview → send → END. The router picks the destination from a fixed list. The short summary that Slack uses is written at save time, so PII is checked only by existing code: at save and at the single exit. The send uses an idempotency key, and the next message starts a normal turn.
  - `deliver` is an isolated subgraph (a report ID and a destination in, an outcome out, no state shared with analysis), so it can grow into its own agent or service without changing the shell.
  - Rejected: a `send_email` analysis tool, even behind human approval (the model holds a send action and picks the recipient: the "lethal trifecta"); a model drafting each message (new text after the save, needing its own checks; kept as a growth step); a delivery sub-agent (an extra model loop, and a model still decides to send); delivery in the Channel Gateway (after the single exit, no access to Saved Reports).
  - Added with the first outbound adapter; not in the prototype.

## Safety & PII (Req 2)

Detail in `docs/hld/03-safety-and-pii.md`; layers are named, not numbered (input guard, then ADR 0003's query guard, data platform, least privilege, output PII filter).

- **Query identity (production):** *Revised 2026-11-14:* SSO identifies the User but gives no BigQuery credentials. The baseline is the read-only service account, with the query guard carrying Brand scoping alone. Per-User queries are an option: a one-time OAuth 2.0 consent with a read-only BigQuery scope, refresh token stored encrypted and revoked on offboarding (12, Q4). One row access policy per Brand, granted to a Google group; a sync job keeps group membership matching the Brand Assignments in Postgres (the source of truth). PII columns carry policy tags; no agent identity holds Fine-Grained Reader.
- **Query guard details:** `sqlglot` (BigQuery dialect); fails closed; exactly one `SELECT` (CTEs allowed), no scripting or `EXECUTE IMMEDIATE`; Brands bound as query parameters; a rejection is a tool error that counts against the SQL retry budget.
- **Out-of-scope Brands are rejected, not filtered:** SQL naming a Brand outside the User's scope returns an "out of scope" result, so the User is told rather than shown an empty or misleading answer.
- **Per-table scope:** `products` by Brand; `order_items` by those products; `orders` containing at least one such item, without order-level columns such as `num_of_item` (a mixed basket would count other Brands' items; basket size comes from scoped `order_items`); `users` who bought at least one such item. Executives get column projection only.
- **Input guard:** six categories (analysis, report management, preference, off-topic, PII request, injection attempt), returned by the same Flash-Lite call as the router; deterministic rules (length, known injection phrases, encoded payloads) run first.
- **Indirect injection through data values** (e.g. a product name with instructions) is handled by passing tool results as delimited data.
- **Output PII filter:** Google Cloud Sensitive Data Protection in production, regex detectors in the prototype; a hit is redacted, traced and alerted.
- **Output PII filter at a single exit** (ADR 0003, amended): one outbound function in the Chat API is the only code that writes to the Gateway stream or calls a `Delivery` adapter (ADR 0009), and it filters every message (progress events, confirmation prompts, catalog messages, final answer, delivered content). `save_report` filters before it writes.
  - Why: as a graph node before `respond`, the filter missed progress events (streamed from inside nodes and tools), the Deletion Plan at `interrupt()`, and Saved Reports written mid-loop, which contradicted "Saved Reports hold only what passed the output filter".
  - Progress events are templates (fixed phrase plus tool and table names) and pass regex detectors only, so they add no Sensitive Data Protection call; every other message passes both.
  - Rejected: filtering in the Channel Gateway (misses Saved Reports, and checkpoints would hold unfiltered text).

## Models (Req 5 cost, Req 8 agility)

- Tiered Gemini models: Flash-Lite for the router, guard and entity extraction; Flash for the analysis agent; Pro allowed for long report generation. The prototype defaults to Flash to stay inside free-tier limits.
- Model IDs and provider settings come from **environment files** (`.env`), not code.
- **One model setting per separate model call** (refined 2026-11-14, LLD): every model call is its own step with `MODEL_<STEP>` and `MODEL_<STEP>_FALLBACK`; an unset step inherits from a parent step. The steps are listed in `docs/hld/10-stack-rationale.md`. Each is in `provider:model` form (e.g. `google_genai:gemini-…`), resolved through one model factory. Swapping a step's model or provider is a one-line `.env` change. Gemini is the default, and other providers stay supported.
- **HLD: model and provider experimentation.** A candidate model or provider is tested by running the offline eval suites per step and comparing scores, cost and latency against the current one in the observability tool's experiments view. It is then rolled out by shadow traffic → canary percentage → full, controlled by config, not redeploys.
- **Detail** (in `docs/hld/10-stack-rationale.md`):
  - `MODEL_JUDGE` defaults to Pro (stronger than Flash analysis; the same tier as Pro reports, which rely on calibration); figure stripping is its own step (`MODEL_TRIO_INGEST`, Flash), separate from entity extraction (`MODEL_ENTITY_EXTRACTION`, Flash-Lite), which settles the 02/10 mismatch; embeddings are their own step.
  - **Why Gemini:** the client tests with it; Flash is strong at SQL and tool use for its price; on Vertex AI it runs in the same project and region as the data.
  - Exact model versions live only in `.env`, never in the HLD.
  - Prototype: Flash for analysis, Flash-Lite for the router, both on the AI Studio free tier.
- **Stack rationale (10):** each GCP service is justified and mapped to a portable equivalent (AWS, Azure, self-hosted); supporting libraries (sqlglot, Pydantic, tenacity, OTel/OpenInference, Phoenix, pgvector, uv, pytest) each have a one-line reason. Choices were made on the requirements, not on familiarity; the author's experience is stated per technology with its delivery risk, and each gap is covered by tests (sqlglot: the safety suite is written before the guard) or kept behind a port (pgvector, Langfuse are production-only).

## SQL self-correction (Req 5)

1. **Dry run first.** A BigQuery dry run is free, catches syntax and schema errors, and returns bytes to be scanned. Anything over the bytes cap is rejected before any cost.
2. **Error → retry.** The error message and the relevant schema go back to the model. At most 2 retries.
3. **Empty result → diagnose once.** One diagnostic step (e.g. check the distinct values of the filtered column: "California" vs "CA"), then one retry.
4. **Budget exhausted → graceful answer.** Explain what was tried and suggest a rephrasing. Never an exception in the UI.

The retry counter lives in graph state, so the cost per question is bounded and visible in traces.

Detail in `docs/hld/06-resilience.md`:
- **One shared SQL retry budget** covers guard rejections, dry-run errors, runtime errors and bytes-cap rejections. A bytes-cap rejection carries a "narrow the period or aggregate" hint.
- **The empty-result probe is code, not a model call:** it reads filter literals from the guard's AST and runs small scoped probes (closest distinct values, table date range); the findings go to the model as a hint. If the filters are valid and the result is still empty, the agent says there is genuinely no data and what it checked.
- **Budget exhausted:** the graceful answer also shares findings from queries that succeeded earlier in the turn.
- **Cost ceilings:** a cap on analysis steps per turn and a turn deadline, besides the SQL budget.

## Golden Bucket (Req 1)

- **Prototype:** a small seed set of 5–10 hand-written Trios with simple embedding search. This is a stretch goal, cut first if time runs short.
- **Ingestion:** expert Trios land in object storage (GCS) → Pub/Sub → worker service → parse, strip figures (ADR 0005), tag Brands, embed → pgvector. Versioned; runs on new files and nightly.
- **Growth, with a human gate:**
  1. Saved Reports with strong signals (thumbs-up, no follow-up corrections) become anonymized Trio Candidates. *Off by default until the client answers whether private reports may be proposed (12, Q1; revised 2026-11-14).*
  2. A human analyst approves each Candidate before it is published.
  3. A nightly dry run of every Trio's SQL catches schema drift and retires broken ones.
- **Query time:** hybrid search (vector + keyword) over Trios the User may see, top-k injected into the analysis sub-agent by the fixed `retrieve_trios` node before the ReAct loop starts; the `search_golden_trios` tool stays for re-search (ADR 0008).
- **Retrieval unit: one Trio, not token chunks.** A Trio is a small, self-contained question → method lesson; splitting it would separate the question from its SQL.
  - **Only the question is embedded**, plus optional analyst-written paraphrases that point to the same Trio.
  - The SQL and the method summary are **payload**, returned with a match but not embedded.
  - Long Analyst Reports are **summarized** at ingestion, not chunked.
  - **Metadata for filtering:** Brands, tables used, question type (comparison, trend, churn, …), Trio version, status (active or retired).
- **The database schema is not part of the RAG.** It is only 4 tables, so table and column descriptions go straight into the prompt through `get_schema`.
- **Ingestion safeguards** (detail in `docs/hld/02-hybrid-intelligence.md`):
  - Trio SQL is dry-run at ingestion; a Trio that does not compile is never published.
  - Figure stripping is verified: after the LLM writes the method summary, a deterministic check looks for numbers, percentages and currency. Any hit quarantines the Trio for an analyst.
  - Brand tags come from Brand literals in the SQL plus entities named in the report. If tagging is uncertain, the Trio is tagged as covering all Brands (Executive-only) until an analyst corrects it.
  - Embeddings use the model factory, with its own setting `MODEL_EMBEDDING`.
- **Retrieval rules:**
  - A User sees a Trio only when every Brand it covers is one of their Brands; Executives see all.
  - Vector and keyword results are fused by reciprocal rank fusion; top 3 above a similarity threshold are returned. No match above the threshold on a turn that runs SQL → the turn is flagged as a *retrieval gap*, which feeds the learning loop and the domain-expert sessions.
  - Trio content is injected as a delimited reference block, never as instructions.
  - Retrieval is a fixed graph step, not a tool call the model may skip: Req 1 says the agent *must* use the Golden Bucket (ADR 0008). The router's standalone search question keeps follow-ups ("and NY?") in context.
- **Versioning and gating:** each edit is a new `trio_id@version`; the old one is marked `superseded`; traces record the exact version used. Approved Trios are activated in batches, only after the offline correctness suite passes.
- **Measuring value:** the offline eval runs with retrieval on and off (ablation); the difference must stay positive.
- **Prototype (stretch):** seed Trios in `seed/trios/`, Gemini embeddings in SQLite, cosine search in Python behind the `TrioRetriever` port; if the slice is cut, `retrieve_trios` calls an adapter that returns nothing.

## Learning loop (Req 4)

- **User level:** a structured User Profile. Explicit statements ("always give me tables") apply immediately. "This time" is a one-off. A pattern repeated about 3 times is inferred by a background job, with a confidence threshold. The profile has three parts:
  1. **Core fields**: format, depth, charts, language. Typed.
  2. **Extensible attributes**: JSONB; new keys need no migration.
  3. **Notes**: a capped list (~20) of short free-text preferences. Length-limited, passed through the input guard, visible to and deletable by the User, injected as data, never as instructions.
  - Aggregation promotes recurring note types into attributes or core fields.
  - **Prototype:** notes only, in SQLite, if time allows (after Trios).
  - **Detail** (in `docs/hld/05-learning-loop.md`): core fields are `format` (table, bullets, prose), `depth` (headline, standard, deep dive), `charts` (never, when helpful, always), `language`; every value records source (explicit or inferred), confidence and last confirmation.
  - Inference runs daily. An inferred preference is announced to the User once ("say 'stop' to undo"); explicit always beats inferred.
  - Precedence: locked safety rules > this-turn > explicit > inferred > Persona defaults. The Persona owns tone and required report sections; the Profile owns presentation (format, depth, charts).
  - User control: "what do you know about my preferences?", `/profile`, "reset my preferences".
  - Metric: preference override rate (how often a User corrects format or depth after the Profile was applied).
- **System level: humans in the loop, never self-rewriting in production.**
  - Every flow (trace, SQL, retries, feedback, rephrasings) is stored.
  - Periodic aggregation jobs cluster questions and failures to find gaps, e.g. new topics, recurring SQL errors, low-satisfaction areas.
  - A weekly review turns findings into new eval cases, Trio Candidates and prompt-change proposals.
  - Scheduled **domain-expert sessions** (analysts and business owners) review the aggregates and add knowledge the logs can't reveal.
  - Every change ships only if the eval set passes.
  - Quality signals also include corrections ("I meant last quarter") and guard blocks the User later reverses.
  - Review outputs also include clearer `get_schema` column descriptions (recurring SQL errors usually mean an unclear schema) and new message-catalog classes.
  - Every real failure becomes an eval case before it is fixed.
  - Domain-expert sessions are monthly.
  - No automatic self-improvement in production: it risks drift, spreading one User's mistakes to everyone, and poisoning through crafted conversations.
  - Metric: share of review findings closed within two cycles.

## Configuration vs. Persona (Req 8)

- **`.env`** holds deployment and engineering config: model IDs and tiers, provider, limits. Developers change it, and a restart is acceptable.
- **Persona** holds tone, report structure and emphasis. It is **not** environment config:
  - It is stored versioned (Postgres or a prompt registry such as Langfuse or LangSmith Prompt Hub).
  - Non-developers edit it in an admin UI with preview and rollback. *(Revised 2026-11-12: only the CEO, in chat or by direct edit; see below.)*
  - The agent loads it with a ~60 s cache, so there is no redeploy.
- Locked prompt layers (safety rules, tool contracts) cannot be edited through the Persona.
- **Prototype:** ~~`persona.yaml`, re-read on every turn~~. *Revised 2026-11-12:* chat or `/persona edit`, the Persona Quality Gate, versions in SQLite read every turn; `persona.yaml` is version 1.
- **Detail** (in `docs/hld/09-persona-management.md`):
  - Locked layers also include the **output contract** (interpretation line, data blocks). Only answer-writing steps read the Persona; input guard and router prompts are fully locked.
  - **Structured Persona, not a free-form prompt:** `tone` (preset + short free text), `report_sections`, `emphasis`, `vocabulary`, `examples`, `messages` (catalog wording; each class keeps its next step).
  - Admin UI access needs a **Persona-editor role**.
  - **Preview** re-writes stored query results for a fixed sample set: a few model calls, no BigQuery scans.
  - **Publish check:** field limits; input-guard injection rules on free text; required sections present; a few red-team cases run through the answer-writing step with the candidate Persona still hold (*revised 2026-11-14:* the router never reads the Persona, so testing it proved nothing); the judge confirms figures are unchanged ("style, never content").
  - **Versions:** each publish is immutable with an active pointer; **scheduled publishing** ("activate Monday 09:00"); an in-flight Conversation switches at its next turn; publish and rollback are audited; quality metrics are broken down per Persona version.
  - **Store:** Postgres by default; a prompt registry is the drop-in alternative behind `PersonaStore` if production uses Langfuse.

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
  - **SLOs with alerts:** see *Scale, async processing & SLOs*.
  - **Synthetic canaries:** 3–5 known questions run end to end every 15 min.
  - **Online evals:** an LLM judge scores ~5% of production turns.
  - **Retrieval health:** the share of turns that run SQL with a Golden Trio above the similarity threshold.
  - The prototype computes the per-turn data; north star, SLOs, canaries and online evals are HLD-only.
- **Detail** (in `docs/hld/08-observability.md`):
  - **OpenTelemetry Collector** between services and backends: batching, a second PII-redaction pass, fan-out to the trace backend and Cloud Monitoring.
  - **Trace contents:** one trace per turn, grouped by `conversation_id`. Root span carries the trace ID (= reference ID), internal User ID (never name or email), role, channel, router action, Persona version, User Profile snapshot, outcome, error class. `run_sql` spans hold SQL before and after the guard rewrite, guard decision, dry-run and billed bytes, row count, attempt, probe findings. Output-filter spans record hit types only. Never result rows or PII.
  - **Metric families** also include *Knowledge* (retrieval health, per-Trio usage) and *Learning* (override rate, findings closed), gathering metrics from 02, 04, 05.
  - **Alert severities:** page at any hour for a PII hit or an out-of-scope answer; page in business hours for fast error-budget burn (multi-window burn rate), a canary failing twice in a row, or a DLQ message older than 5 days; ticket for quality drop, unclassified errors, breaker open, DLQ messages, spend at 80%.
  - **Canaries** run as a test User with known answers.
  - **Debug workflow:** find the turn (ref ID, alert, failure cluster) → read the step tree → replay from the LangGraph checkpoint in a sandbox with the candidate fix → save as an eval case → ship through the release gate.
  - **Prototype:** `/trace` prints a step tree (example in 08). Not in the prototype: collector, dashboards, alerting, canaries, online evals, replay tooling.
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
- **Four failure classes, one response each** (detail in `docs/hld/06-resilience.md`): self-correctable (SQL loop), transient (backoff, then fallback), permanent for this request (catalog message, no retry), unclassified (catch-all).
- **Starting values (configuration, tuned from traces):** timeouts ~10 s Flash-Lite, 60 s Flash/Pro, 60 s BigQuery job, 5 s Postgres; up to 3 attempts honoring `Retry-After`; a breaker opens after ~5 failures in 30 s, then probes after a cool-down.
- **Only idempotent calls are retried.** Writes are idempotent instead: `save_report` dedupes on Conversation + turn, `execute_deletion` on the plan ID.
- **The rate limiter waits** briefly rather than failing; if the wait exceeds the turn budget, the User gets the "busy" message.
- **Per-dependency fallbacks:**
  - Gemini: Flash → Flash-Lite; production adds a second Vertex AI region, then a second provider, via settings such as `MODEL_ANALYST_FALLBACK`.
  - Input guard and router **fail closed** if every model is down; safety steps are never skipped.
  - BigQuery down: no stale data served as fresh; Saved Reports and preferences keep working.
  - Postgres down: fail closed (no identity, no checkpoint); Cloud SQL HA.
  - Trio search down: continue from schema only, turn flagged degraded.
  - Persona store down: last cached version, else the bundled default.
  - Sensitive Data Protection down: regex detectors as fallback; the PII filter is never skipped. Accepted trade-off: narrower detection, not none, because PII columns are blocked upstream and failing closed would stop every answer; each activation is alerted.
  - Pub/Sub publish failure: retried; nightly sweeps redo missed work (e.g. entity extraction backfill).
  - Observability backend down: async batched export, dropped on overflow; tracing never blocks a turn.
- **A fallback model must pass that step's offline eval suites** before it joins a fallback chain.
- **The Gateway has its own deadline** and sends the "taking too long" message itself.
- **Catalog additions:** "query too large" and "daily quota reached". The reference ID is the trace ID.
- **Prototype:** includes an in-process circuit breaker per dependency and failure-injection `pytest` cases through fake adapters. Not in the prototype: second region/provider, Gateway deadline, result cache, per-User quotas, spend cap.

## Quality assurance (Req 6)

- **HLD, three suites:**
  1. **Correctness:** execution match against reference SQL, plus an LLM judge with a rubric for intent.
  2. **Safety red team:** the gate is a 100% pass rate.
  3. **UX:** executive pilot sessions plus online signals (CSAT, turns to answer, rephrase rate, abandonment).
- **Prototype:**
  - A `pytest` safety suite (query guard, scoping, PII filter, Deletion Plan logic).
  - `make eval`: ~12 questions (8 correctness from the spec categories, 4 safety), scored by execution match or an LLM judge. Prints a scorecard and pushes it to Phoenix if running. Rate-limited for the free tier.
- **Detail** (in `docs/hld/07-quality-assurance.md`):
  - **Five layers:** deterministic `pytest` (every commit, offline); per-step evals (router/guard classification, entity extraction, figure stripping; when that step changes); end-to-end correctness and safety red team (every release candidate and nightly); cost and latency budgets from the eval traces.
  - **Eval set sources:** seed cases from the spec categories; a **held-out share of Golden Trios** kept out of retrieval as correctness references; real failures; red-team cases plus the input-guard POC set. Each case records the User it runs as (role, Brands).
  - **Execution match:** order-insensitive, column names ignored, numeric tolerance.
  - **Judge rubric** (`MODEL_JUDGE`, Pro: stronger than Flash analysis, the same tier as Pro reports): intent, grounding (every figure from a query result), method and caveats, completeness (Persona sections, action items), presentation (Profile). The judge is calibrated against human labels before it gates anything, and its agreement is tracked.
  - **Nondeterminism:** each case runs ~3 times; the score is the pass rate.
  - **Safety cases are judged from the trace, not the wording:** no out-of-scope rows, no PII in output, no DELETE without a confirmed plan.
  - **Gate:** reject on safety < 100%, any correctness regression vs the current version, or cost/latency over budget. Gated: prompts, models/providers, new tools, Trio batches, fallback models. Persona edits get a lighter check in the Admin UI (`09`).
  - **Intent at runtime:** every analytical answer opens with one line stating its interpretation ("I read 'last quarter' as Q3 2026…"). Rephrased, corrected or abandoned turns are prioritized for human review and become eval cases.
  - **UX pilot:** 5–8 executives and Managers, moderated, fixed task list (comparison, "why" drill-down, Q1 report with action items, deletion); sets the first trusted-answer baseline. Online UX signals also include latency vs SLOs, delete-flow rates and preference override rate.

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
  - **Worker service (Pub/Sub push) + Cloud Run jobs (Cloud Scheduler):** Trio ingestion on the worker; nightly Trio validation, weekly aggregation and synthetic canaries as jobs. *Revised 2026-11-14:* a Cloud Run job cannot receive a Pub/Sub push.
  - **Data:** Cloud SQL Postgres, GCS (Golden Bucket), BigQuery.
  - **Secrets and network:** Secret Manager; a private VPC connector; IAP/SSO in front of the web UI and Admin UI.
  - **Scaling:** stateless services; all state is in Postgres and the checkpointer. Each box is a container, so it is portable to any container host.
- **Cost controls:**
  - Per-User daily quotas (tokens, BigQuery bytes).
  - A global daily spend cap: at 80% an alert and background jobs pause; at 100% new turns get "daily quota reached" until midnight.
  - `maximum_bytes_billed` on every query.
  - Result caching keyed on the final SQL, its bound parameters and the User's scope version, valid until the next data load, plus BigQuery's own cache. *Revised 2026-11-14:* the question text alone does not identify a follow-up such as "and last month?".
  - The prototype enforces only the per-query bytes cap.
- **Long Conversations:** the last N turns are kept verbatim plus a running summary of older turns. Large results never go into history; a compact result summary does. Result rows are never stored: not in the turn record, the interaction store or traces; only the result's shape is.

## Scale, async processing & SLOs

**Sizing (from the client):** ~1,000 questions/day, ~100 Saved Reports/day, only a few concurrent Users. At ~5–10 LLM calls per question that is ~5–10k LLM calls/day, peaking at tens per minute. This is a **low-scale internal tool**, so the design is right-sized: no high-scale machinery, but every growth path is named.

- **Background jobs** (Trio ingestion and validation, entity extraction, preference inference, Trio Candidates, aggregation, canaries):
  - Pub/Sub with a **dead-letter topic** per job type. After N delivery attempts with backoff, the message moves to the DLQ, an alert fires, and it can be inspected and replayed once fixed.
  - **What a dead-lettered message holds.** *Revised 2026-11-15:* the DLQ is a store too, so its data is now decided.
    - **Events carry IDs only:** `report.saved` and `report.deleted` carry `report_id` + Author; `turn.completed` carries `conversation_id` + turn number; a Trio file event carries the GCS object name + generation. The handler reads content from Postgres or GCS. A DLQ message therefore holds **no PII and no content**, and a replay acts on current state: replaying an event for a report deleted since is a no-op.
    - **Only event work dead-letters.** The DLQ sits on each push subscription (Trio ingestion, entity extraction, report cleanup, Trio Candidates, interaction-store write). Batch jobs on Cloud Scheduler are retried by Cloud Run and alerted; chat turns are never dead-lettered.
    - **N = 5, approximately:** the Pub/Sub default; the range is 5–100, and the count is best-effort ([dead-letter topics](https://docs.cloud.google.com/pubsub/docs/dead-letter-topics)).
    - **Kept 7 days** on a pull subscription attached to each dead-letter topic, the Pub/Sub default retention (max 31 days, [subscription properties](https://docs.cloud.google.com/pubsub/docs/subscription-properties)). The subscription is required: a topic with no subscription loses what is published to it. It is set never to expire, because its default 31-day expiry would remove a quiet DLQ subscription.
    - **IAM:** the Pub/Sub service agent gets `roles/pubsub.publisher` on each dead-letter topic and `roles/pubsub.subscriber` on each source subscription; without them nothing is forwarded.
    - **Replay:** after the fix, an operator re-publishes the DLQ messages to the source topic and acknowledges them in the DLQ subscription. Handlers are idempotent, so a double replay is safe.
    - **Alerts:** a ticket on the first DLQ message; business-hours page when the oldest DLQ message is older than 5 days, because the 7-day window is about to close.
  - Event work runs on a worker Cloud Run service behind Pub/Sub push; the handler acknowledges (returns 2xx) only after its work commits. Scheduled batch work runs as Cloud Run jobs on Cloud Scheduler.
  - Delivery is at least once, so **every job is idempotent**, with a dedupe key such as `trio_id + version` or `report_id`.
- **Chat turns: no queue at this scale.** The Gateway acknowledges the channel immediately and runs the turn asynchronously in-process; Cloud Run with CPU always allocated is enough.
  - A turn that fails after retries **fails fast** to the User with a friendly message and a reference ID. It is never dead-lettered for later replay, because a late answer to an old question confuses the User.
  - **A stopped instance is not resumed.** *Revised 2026-11-14:* "restarts lose nothing" overstated it. Checkpoints keep history and a paused Deletion Plan; a running turn has a status and deadline, the Gateway sends the failure message, and BigQuery jobs carry a job timeout. Cloud Run can stop any instance with a short grace period, so guaranteed resume needs the queue below.
  - **Growth path:** if concurrency grows, put a task queue (Cloud Tasks or Pub/Sub) between the Gateway and the workers for backpressure.
- **Growth path: MCP** ([ADR 0007](adr/0007-in-process-tools-mcp-as-growth-option.md)). Not in the design today. Adopted when extension capabilities ship as MCP servers, or when a second consumer needs governed data access (a governed-data MCP server enforcing guard, scope and PII itself).
- **What limits us (and the growth path for each):**
  - **Gemini quota:** the per-process rate limiter is enough at this scale. Growth: a shared limiter (Memorystore token bucket) and provisioned throughput.
  - **Batch jobs competing for quota:** they run off-peak, with their own budget. Interactive traffic always has priority.
  - **BigQuery cost:** the bytes cap, a result cache, per-User quotas. It relies on date partitioning from the data team. Growth: reserved slots.
  - **Postgres connections:** Cloud SQL connector pooling and a max-instances cap. Growth: PgBouncer, a read replica for report search.
- **Data freshness:** data loads daily, so the result cache is valid until the next load, and answers state "data as of <last load>".
- **Cost estimate:** the HLD includes a back-of-envelope daily cost (LLM tokens × current pricing + BigQuery bytes) for ~1,000 questions/day, computed from the trace data.

**SLOs (ours to define; derived from the client's response-time expectations):**

| SLO | Target | Why |
|---|---|---|
| Simple-question latency | p95 ≤ 30 s | The client expects 10–30 s. Progress messages stream meanwhile. |
| Report latency | p95 ≤ 120 s | The client expects 1–2 min. |
| Availability | 99.5% during business hours | Internal tool: about 1–1.5 hours of degraded service a month during business hours (~220–260 h) is acceptable; no 24/7 on-call. |
| Trusted-answer rate | ≥ 85% (re-baselined after the pilot) | Starting point from the offline eval; tightened as data accumulates. |
| PII leaks in output | 0 (any hit pages immediately) | Hard requirement, not a budget. |
| Unauthorized-scope answers | 0 | Hard requirement. |

- **Error-budget policy:** while a latency or availability budget is exhausted, reliability work comes before new features.
- **Detail** (in `docs/hld/11-operations.md`):
  - **Deployment:** one region, next to the governed BigQuery dataset (a second Vertex AI region for fallback only). Separate dev, staging and production projects from the same infrastructure-as-code; staging runs canaries and the release gate.
  - **Delivery:** CI builds one image per commit, runs the deterministic tests, pushes to Artifact Registry. Canary and rollback are Cloud Run traffic splits between revisions; prompt, model and Persona changes need no deploy.
  - **Chat API:** internal ingress only, min 1 instance (no cold start), max instances capped by the Postgres connection limit. Only the Gateway and the IAP-protected Admin UI are public.
  - **Peak:** ~100 questions/hour, ~5/min in the busiest hour, ~2 turns at once; one I/O-bound instance is enough.
  - **SLO measurement:** latency is Gateway receive → final reply; availability is the share of turns ending in an answer rather than an infrastructure-failure message, plus canary success. Correct refusals (scope, PII, quota) do not count against availability.
  - **Cost estimate (assumed inputs, recomputed from traces later):** Flash-Lite $0.25/$1.50, Flash $1.50/$9.00, Pro $2.00/$12.00 per 1M tokens; BigQuery $6.25/TiB; ~5 Flash calls × ~8k in per question (Trio retrieval is a fixed step, not a model call, ADR 0008); ~2.5 executions × ~2 GB per question. Result: ~$123 per business day for Users, ~$11/day canaries, ~$650/month fixed infra → **~$3,700/month, ~$0.17 per question**. Analysis is about half; doubling the Flash price or steps adds ~$1,800/month.
  - **Cost-control starting values:** `maximum_bytes_billed` ~20 GB; per-User quota ~5× a typical User's day; global cap ~3× expected daily spend (80%: alert and background pause; 100%: new turns refused); background jobs off-peak with batch pricing where available.
  - **Canaries:** 4 short questions every 15 min in business hours, hourly outside (~220 runs/day instead of ~380), because canaries would otherwise be a top-three cost.
  - **Security:** roles are Executive, Manager, Persona editor, analyst (Trio review), AI-ops (raw data), default deny; one service account per service; VPC Service Controls perimeter around BigQuery, GCS, Vertex AI; Google-managed encryption, CMEK optional; Vertex AI terms exclude training on customer data; Cloud Audit Logs; `uv.lock`, image scanning, deploys only from CI.
  - **Recovery:** Cloud SQL point-in-time recovery (RPO minutes), HA failover in about a minute, quarterly restore drills; Persona and Trio versions are never overwritten.
  - **Running it:** AI-ops owns the agent (assumption); weekly review, DLQ replay, monthly domain-expert session, quarterly restore drills and quota reviews.
- **Prototype:** computes the per-turn data behind these SLOs (latency per turn and node, success, guard and PII-filter hits) in the traces. Dashboards and alerting are HLD-only.

## Assumptions and open questions (12)

Detail in `docs/hld/12-assumptions-and-open-questions.md`, which supersedes `open-items.md` as the single list.
- Every assumption records where it is used and **what changes if it is wrong**, so a "no" from the client is a known adjustment, not a redesign. Every client question carries **our default until answered**.
- Added in 12: Vertex AI available in the dataset's region (A7); business hours = one time zone, ~10 h, Mon–Fri, quotas reset at midnight there (A11).
- Added client questions: source of Brand Assignments (HR/CRM, spreadsheet) and who changes them; dataset region and partitioning; Users' time zones; how many Trios exist and in what format; which channel first; which fallback provider is acceptable.
- Defaults: ~~Persona editor is a named editor in the CEO's office~~ (revised 2026-11-12: only the CEO holds `persona_editor`); one or two analysts give a few hours a week for Candidate review and judge labels.
- Fallbacks if wrong: Brand Assignments imported by a sync job from HR/CRM; an authorized view as the minimum if no governed dataset; cache invalidated per load event if loads are more frequent; without human labels the judge reports but does not gate.
- **To measure, not to ask:** cost inputs, timeouts and breaker values, Trio similarity threshold, quotas and caps, trusted-answer baseline.

## Mock-interview audit follow-ups (2026-11-12)

- **Persona changes (Req 8), client-confirmed by Harry:** a non-technical User such as the CEO updates the instructions without code edits or redeploy; the mechanism is ours; every change passes an automated quality gate with **no manual approval**.
  - **Only the CEO** holds the `persona_editor` permission, checked in code from a `user_permissions` table. Delegation is an admin grant, not a code change (Q9).
  - **Two paths, one gate:** the CEO asks in chat ("make the reports warmer"), or edits fields directly (`/persona edit` in the CLI, the Admin UI in production). Both pass the same Persona Quality Gate: field limits, injection scan, red-team refusals, required sections, figures unchanged.
  - A pass goes live at once; undo is one sentence or `/persona rollback`, and skips the gate. A fail keeps the current version and says why.
  - The chat path is a **fixed branch** opened only by the router, never a tool of the analysis agent. Rule (ADR 0004): an action that destroys data or changes behavior for every User is a branch, not a tool.
  - Prototype: the figure check is code comparing numbers against a stored result, instead of the production judge.
- **Agent-level metrics in the prototype:** `/stats` (and `agent stats --since 7d`) aggregates the JSONL traces into the 08 metric families. It shows task success instead of the trusted-answer rate, because the CLI collects no feedback.
- **LangSmith** is a production trace-backend option next to Phoenix and Langfuse; self-hosting it is an Enterprise-plan add-on, and its SaaS needs client approval. The prototype stays on Phoenix.
- **Canary deployment** is out of the assignment's scope: described once, in 11 Delivery; other sections link there. Synthetic canaries (monitoring probes) stay.
- **Consistency fixes:** the ADR 0004 tool list, the 03 guard classes matching the router's actions, and the 08 canary schedule matching 11.

## LLD (Phase 2, 2026-11-14)

Code-first: the schemas, Protocols and DDL in `src/agent/` are the source of truth; `docs/lld/` explains them. Approved additions beyond the HLD and ADRs:

- **Two ports added to ADR 0006's list:** `UserDirectory` (identity for the load-context step) and `Delivery` (declared, not wired; ADR 0009).
- **Two tables added:** `trio_embeddings` (one vector per question and paraphrase) and `persona_events` (publish, rejection and rollback records). Embeddings are 768-dimensional to stay inside pgvector's HNSW limit.
- **One model setting per separate model call** (see Models above): 12 `ModelStep`s, each with `MODEL_<STEP>` and `_FALLBACK`; unset steps inherit. The delete filter is its own call (`MODEL_DELETION_FILTER`), and the Persona gate follows the analysis model unless set on purpose.
- **Starting limits** marked "LLD default" in `config.py`: 200 rows to the model, 2 GiB bytes cap, 8 analysis steps, 90 s turn deadline, 0.75 Trio score, 6 verbatim turns, 30 s breaker cool-down. Tuned from traces.
- **Contracts:** another User's report is "not found", never "forbidden"; catalog keys added for `no_role`, `unavailable`, `plan_expired`, `report_not_found`, `report_out_of_scope` and the Persona outcomes.
- **One core for every channel:** inbound goes through `ChatService.handle(message, channel)`, model-free commands through `Views`; `ChannelAdapter` is outbound-only and per turn. The CLI and the optional web UI only translate, so they offer the same features (LLD 07).
- **Web UI (optional slice 8):** a plain `web/` folder, types generated from Pydantic, no Nx (ADR 0010). `/stats` is limited to Executives and `/trace` to the User's own turns.
- **Enforced in tooling:** a ruff rule bans provider SDK imports outside `agent.adapters`; a test fails if any tool input names a User, Author or scope, or if a model step lacks its settings.

## Evals and seed data (2026-11-15)

Approved additions in LLD 08 (built with the eval-genius skill's rules):

- **Fourth new port, `ScorecardSink`:** eval runs go to `evals/runs/<run_id>.json` and, when it runs, to Phoenix (cases as a dataset, a run as an experiment, compared side by side, each case linked to its trace). Runs start from `make eval`, not from the Phoenix UI, so the eval drives the same `ChatService` path as Users.
- **Measurement rules in the types:** PASS / FAIL / CANNOT_MEASURE (exit 0/1/2); deterministic checks gate, the judge reports until calibrated, the trajectory is reported only; pass@k next to pass^k; a negative control that must fail; safety reported per attack class.
- **The bar is pre-registered** in `evals/PREREGISTRATION.md`: safety and session cases pass on every trial; no per-item correctness regression against a baseline; any CANNOT_MEASURE in safety or session makes the run CANNOT_MEASURE.
- **A session suite** grades the delete flow on the store state afterwards. **Execution match passes when any query in the turn matches the reference**, and reference columns need only be a subset of the agent's.
- **Real seed data** from `thelook` (queried 2026-11-15): `manager-a` = Calvin Klein, Tommy Hilfiger; `manager-b` = Carhartt, Columbia, The North Face; Brand values kept with their exact case (`adidas`, `PUMA`). Five seed Trios, two tagged with one Manager's Brands each. Twenty cases; every reference query and Trio query ran on BigQuery.
- **Data is used as it is.** Queries read the table as loaded; answers say "data as of <latest date in the data>". The public dataset holds generated rows dated a few days ahead (latest 2026-11-18 on 2026-11-15); no filter is added for them.
- Facts from the data that shaped cases: 51% of orders with manager-a's Brands hold another Brand (the `num_of_item` rule); California and Texas spend per Customer differ by about 0.2% (a case whose right answer rejects the premise).

## Slice 3: resilience (2026-11-15)

Approved deviations from issue #7:

- **Circuit breaker per model, not per dependency.** Gemini breakers and rate-limit buckets are keyed by model, so when Flash is down or out of quota, Flash-Lite (its fallback) can still answer. With one breaker for all of Gemini, the fallback would be switched off exactly when it is needed. BigQuery keeps one breaker.
- **Giving up after the data cap says the question covers too much data** (`QUERY_TOO_LARGE`), not the generic "couldn't find a reliable answer". It tells the User what to change (a shorter period, one Brand); HLD 06's catalog has this message for this case. The trace records which message was used (`sql.gave_up`) and the technical reason (`sql.retry_kind`, the byte count in `error`).
- **Catalog wording has no technical terms.** The Users are non-technical executives (ASSIGNMENT.md), so "query", "scan" and "SQL" were removed from the bundled messages, and the analyst is told to explain a give-up in plain words.

## Slice 4: Saved Reports and deletion (2026-11-15)

Approved deviations from issue #8:

- **`save_report` without a query this turn is `needs_query`, not a `ToolError`.** A `ToolError` ends the turn, and "save this" right after an answer is the common case. The model is told to run the query behind the report in this turn, so the scope still comes from the guard, never from the model.
- **Entities in a filter match a report that mentions any of them**, and the reason names which ("mentions: Calvin Klein, Tommy Hilfiger"). Entities, topic, dates and "this conversation" still combine with AND. Entity matching uses a small alias table (`CK`, `TNF`, `Levi Strauss`) next to case and punctuation folding.
- **A reply shaped like an answer (`yes`, `no`, `yes except 2`) with no plan open is `PLAN_EXPIRED`**, if the Conversation has shown a plan before. This is the issue's "a later `yes` → `PLAN_EXPIRED`". It never reaches the router, so a stale "yes" is never guessed at.
- **A non-answer re-enters at `load_context`**, just before the input guard, so the new message gets fresh turn keys and a freshly loaded User. A second bare `yes` to a large plan cancels it like any other non-answer. Cancelling drops the deferred request that came with the delete: the User has moved on.
- **Only the plan's User can resume it.** `ChatService` answers anyone else with `PLAN_EXPIRED` without resuming, so another User can neither confirm nor cancel the plan.
- **A deferred request is sent as its own reply** after the branch's reply. The turn then delivers two `AgentReply`s: the deletion outcome, then the answer.
- **Port changes:** `save` takes the `channel` (the first save records the Conversation that the `saved_reports` foreign key needs); `delete_with_audit` takes the filter (the `deletion_audit.filter` column) and returns a `DeletionReceipt` with `replayed`. `TurnContext` gains `channel`; `Views` takes `limits`.
- **The `REPORT_OUT_OF_SCOPE` wording** no longer promises a title it does not show: "…so I can't show what's in it. You can still delete it."

## Slice 5: observability (2026-11-15)

Deviations from issue #9, for approval:

- **`Tracer` gains `read(since)`.** `Views.trace` and `Views.stats` read spans back through the port, not from the files, so `core/` never touches `traces/` and tests can hand `/stats` a fixed set of spans. The JSONL adapter reads its own files; the Phoenix exporter is write-only.
- **Phoenix is a second sink of the JSONL tracer, not a second tracer.** Each finished `SpanRecord` is rebuilt as an OTel span with the same trace and span IDs and queued on the SDK's `BatchSpanProcessor` (bounded queue, background thread, drops when full). The reference ID a User quotes therefore finds the turn in Phoenix too. Phoenix is used only if `PHOENIX_ENDPOINT` (default `http://127.0.0.1:6006`) answers at start-up; a Phoenix that stops later fails in the background and is logged once at ERROR.
- **Root span gains `message_class`.** `/stats` needs input-guard blocks by category, and the rules block before the router runs, so `router.verdict` cannot carry them. `outcome` is one of `answered`, `confirmation`, `refused`, `gave_up`, `error`.
- **`query_guard` spans carry `sql.written` and `sql.rewritten`**, both through the PII filter: a literal can quote a Customer's email the User typed. `run_sql` still holds no SQL text.
- **A cancelled Deletion Plan is a `deletion_reply` span** (`plan_id`, `reply`, `outcome=cancelled`): a decline never reaches `execute_deletion`, which LLD 05 listed as the place for `cancelled`.
- **A span's error keeps its class and type, never its text.** Classified errors wrap provider messages (`BadRequest: …`) that can quote a Customer literal. Free text that does go on a span (`sql.written`, `sql.rewritten`, the `run_sql` `error`, the report and deletion `filter`) passes the PII filter first. Review found a dry-run error quoting an email reaching JSONL before this.
- **A new turn's input clears the turn keys**, as `load_context` does, so a turn that fails before `load_context` does not report the previous turn's router or retrieval on its root span.
- **A replayed `execute_deletion` is not counted again** in `/stats`.
- **Phoenix full-content mode (`PHOENIX_FULL_CONTENT`, off by default).** Phoenix showed attributes but empty Input/Output panes, so a turn's flow (prompt, tool calls, tool results, answer) could not be read there. We chose OpenInference's LangChain instrumentation over writing inputs and outputs into our own spans: plug and play, and it shows the full content a debugging session needs. It sends rows and unfiltered text, against HLD 03 and the `Tracer` rule, so it is a developer-machine switch only; JSONL, `/trace` and `/stats` keep the rule. Our spans are made the current OTel span while they run, so the instrumented spans nest under our `turn`. Details: LLD 06 § Tracer.
- **`/stats` covers the HLD 08 families that the traces of slices 1–5 can feed:** besides the sample's lines, $ per question, the LLM error rate (model calls where no model answered), the BigQuery error rate (turns ending as `DATA_SOURCE_DOWN`), p50/p95 per graph node, and turns and task success per router action. Deferred to the slice that produces the signal: usage and trusted-answer rate per `trio_id@version` (#11, Trio retrieval), the Learning family (#13), Persona gate rejections and rollbacks (#12). The online judge score and the trusted-answer rate need a judge and feedback the CLI does not collect (HLD 08 already shows task success instead).
- **`llm.cost_usd` is not on the span.** `/stats` prices tokens from `MODEL_PRICES` (USD per million tokens by model ID, empty by default), so a price change re-prices past turns and the adapter stays as it was. With no prices, the dollar figure is "n/a".
- **`agent stats` needs `--as <user_id>`.** `/stats` spans every User and is for Executives (`STATS_FORBIDDEN` otherwise, new catalog class), so the command outside the chat needs to know who asks.
- **`Views.trace` returns `None`** for a missing turn and for another User's turn alike, so the reply cannot tell them apart.
