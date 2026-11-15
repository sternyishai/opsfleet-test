# Progress

The session-to-session handoff. Updated at the end of every session (see [work-plan.md](work-plan.md#how-context-moves-between-sessions)). Newest state at the top; history kept short.

## Current state (2026-11-15)

**Phase:** 4. Slices 1 (#5), 2 (#6), 3 (#7), 4 (#8, PR #22) and 5 (#9, PR #24 open) built. Next: merge PR #24, then slice 6 (#10).
**Branch:** `feat/9-observability`

| Step | Status |
|---|---|
| Phase 0: design foundation | ✅ merged (PR #1) |
| Phase 1: HLD `00`–`12`, ADRs 0001–0009, diagrams | ✅ merged (PR #2) |
| External design review | ✅ fixed in 2ae9e0f: Persona gate check 3 tests the answer-writing step; running turns are not resumed after a restart; service account + query guard is the BigQuery baseline (per-User OAuth is an option); Saved Report `scope` re-checked on use; Trios keep method parameters; hard delete defined (Q17); Pub/Sub pushes to a worker service; Deletion Plan bound to User and latest plan; Trio Candidates off until Q1 |
| `/mock-interview interview` rehearsal | deferred until the prototype runs, so answers can point to working code |
| Phase 2: LLD | ✅ committed (165e81d … b0bbc30): `uv` skeleton, Pydantic contracts, 12 `Protocol` ports, graph state, stubbed tools and core modules, Postgres + SQLite DDL (Postgres verified in `pgvector/pg17`), ER diagram, `docs/lld/01`–`07`, 12 passing tests |
| One model setting per call | ✅ 12 `ModelStep`s with inheritance; HLD 10 table and 02 updated; settles the figure-stripping model (`MODEL_TRIO_INGEST`, Flash) |
| ADR 0010: UI in a plain `web/` folder | ✅ optional slice 8 in the work plan; CLI and UI share `ChatService` and `Views` (LLD 07) |
| Phase 3: GitHub issues | ✅ #5–#14 labelled `ready-for-agent`, native blocked-by links; slice 7 split into #11 Trios, #12 Persona, #13 Profile notes |
| Build guardrails | ✅ `CODING_STANDARDS.md`, `.githooks/pre-commit` (ruff, pyright, pytest), pyright in dev deps; Phase 4 prompt is now `/mattpocock-skills:implement #N` |
| DLQ, story map, ADR 0011 (`docs/dlq-data`) | ✅ events carry IDs only; DLQ kept 7 days on a never-expiring pull subscription, ~5 attempts, IAM, replay, 5-day ageing page; DLQ drawn in the hero and data-flow diagrams; new story map (8 stories) + component × store table in 01; preference inference reads the interaction store; nightly sweep runs on the Worker; ADR 0011 sqlglot + dry-run blind spot under row-level security |
| Eval LLD + real seed data (PR #4) | ✅ LLD 08, `evals/` (20 cases, pre-registered bar), `seed/users.yaml`, 5 seed Trios; all queries verified on BigQuery via project `opsfleet-prototype` (ADC set up) |
| Slice 1: walking skeleton (#5, PR #17) | ✅ `agent chat --as <id>` → graph → Gemini → `run_sql` on BigQuery; CEO 2025 revenue $2,358,149.49 and manager-a Calvin Klein 2025 $46,399.46, both matching direct BigQuery queries; 66 offline tests. Real adapters: SQLite users, sqlglot guard (no scope rewrite yet), BigQuery runner, Gemini, file Persona, JSONL tracer, stdout channel; null Trio retriever and pass-through PII filter |
| Slice 1 retro | ✅ ruff bans `sqlglot`, `yaml`, `langgraph.checkpoint.sqlite` and `agent.adapters` outside adapters/composition/tests; CI workflow; Gemini SDK gotchas in LLD 06 § LLMFactory; `CLAUDE.md` pointer for building a slice |
| Slice 2: safety (#6) | ✅ full query guard (node-type allowlist, PII and order-level columns by name, out-of-scope Brand literals case-insensitively, per-role scope rewrite with `@brands`, forced `LIMIT`), guard span attributes, `RegexPiiFilter` behind `Outbound.emit` and `output_filter`, input rules before the router; 198 offline tests. New `RejectReason.UNSUPPORTED_SYNTAX`; model-written query parameters are rejected (the model inlines a Trio's parameters) |
| Slice 2 retro | ✅ never-raises test over hostile input; `CODING_STANDARDS` §2: safety changes get an adversarial Spec review; ruff skips `ASSIGNMENT.md`; sqlglot notes in LLD 06; branch name in `CLAUDE.md` |
| Slice 3: resilience (#7, branch `feat/7-resilience`) | ✅ `run_sql` guard → dry run → execute with one shared budget, `QUERY_TOO_LARGE`/`SELF_CORRECTION_EXHAUSTED` give-up with a plain `tried` list, empty-result probe in code (`tools/probe.py`) from guard filter literals, insisted out-of-scope Brand → `OUT_OF_SCOPE` ends the turn; `adapters/resilience.py` (tenacity retry honouring `Retry-After`, breaker, token bucket) behind both the Gemini factory (fallback chain → `MODEL_BUSY`, `llm` spans) and the BigQuery runner (client retries off); 282 offline tests. Live: CEO 2025 revenue still $2,358,149.49; a free-tier 429 ends as `MODEL_BUSY` with `llm.errors` naming it. Review fixed: half-open breaker stuck after a non-retryable probe, raw transport errors unclassified, filters attributed to the wrong table, `brand` compared with an expression slipping past the refusal |

| Slice 4: Saved Reports + deletion (#8, branch `feat/8-saved-reports`) | ✅ `SqliteReportRepository` (FTS5, same file as the users) + in-memory fake, both under one test suite; `save_report` (PII filter on every field, guard scope, entity extraction with `MODEL_ENTITY_EXTRACTION` failing soft), `list_my_reports`, `get_my_report` bound to the analyst; `parse_reply`; deletion branch `plan_deletion` → `confirm_deletion` (`interrupt()`) → `execute_deletion` → `after` (deferred request); `ChatService` resumes a paused Conversation; `/reports`; 438 offline tests. Deviations in `decision-log.md` § Slice 4. Adversarial review fixed: a punctuation-only topic matched every report, ticking every box on a large plan skipped the count, Unicode digits and multi-line replies were read as answers. Live demo done (screenshot in PR #22): manager-a saved two reports and deleted one by Brand with `yes except`. Found live: the prompt always suggested "yes except 2", even for a one-report plan; it now names an item in the plan |

| Slice 5: observability (#9, PR #24) | ✅ `JsonlTracer` with span and parent IDs and `read(since)`; a span per graph node (`_traced`), root attributes set from the checkpoint after the turn (a failed turn still says who and what); `sql.written`/`sql.rewritten` PII-filtered on `query_guard`; `deletion_reply` span for cancelled plans; `/trace [id]`, `/stats [7d]`, `agent stats --as <id>` through `Views` and `core/observability.py`; Phoenix as a second sink (`tracer_phoenix.py`, OpenInference attributes, same IDs, used only if it answers at start-up); `STATS_FORBIDDEN`; `MODEL_PRICES`; 467 offline tests. Review fixed: a dry-run error quoting a Customer email reached JSONL (span errors now keep class and type only; free text is PII-filtered), a failure before `load_context` reported the previous turn's router, replays counted twice. `/stats` then gained $ per question, LLM and BigQuery error rates, p50/p95 per node and success per intent; Trio, Learning and Persona metrics wait for #11–#13. Live: CEO 2025 revenue $2,358,149.49, `/trace` showed the router call, a 429 on Flash falling back to Flash-Lite, and the guarded SQL. Export checked against a local OTLP receiver (same trace ID as the reference ID; a vanished receiver leaves the turn unchanged). Phoenix seen live, with an opt-in full-content mode (`PHOENIX_FULL_CONTENT`, LangChain instrumentation: prompts, tool input and output, rows included; developer machines only). Deviations in `decision-log.md` § Slice 5 |

## Decisions made this session, not yet written elsewhere

- None. Slice 3's approved deviations (breaker per model, `QUERY_TOO_LARGE`, plain catalog wording) are in `decision-log.md` § Slice 3.

## Open questions

- 17 client questions (Q1–Q17) in `docs/hld/12`; each has a default, so none blocks Phase 3.
- Not decided in the design: what triggers the Trio Candidate job, and whether an approved Candidate goes through ingestion. Candidate generation is off by default until Q1 is answered, so this does not block the prototype.
- Not decided in the design: who writes chart images to GCS (proposal: a future chart tool writes `charts/<report_id>/…` at save time), and whether an approved Trio Candidate becomes a GCS file so ingestion stays the single path (proposal: yes).
- Cost estimate inputs in `11` are list prices; recompute from prototype traces in Phase 4.
- The "LLD default" limits in `config.py` are starting values; tune them from traces in Phase 4.

## Next session prompt

> /mattpocock-skills:implement #10

Notes for what comes next:

- Phoenix seen live. With `PHOENIX_FULL_CONTENT=true` (set in the local `.env` only) it also shows full prompts, tool calls and tool results, rows included: a developer-machine switch, see LLD 06 § Tracer.
- `MODEL_PRICES` is set in the local `.env` (list prices 2026-11-15: `gemini-3-flash-preview` $0.50/$3.00, `gemini-3.5-flash-lite` $0.30/$2.50 per million tokens); the code default stays empty.
- Before #10: rebase-merge PR #24 (approve the Slice 5 deviations first), then start from `main`. #10 pushes scorecards to Phoenix experiments; Phoenix runs on port 6006.
- Old trace lines (before slice 5) have no span IDs; `read` skips them.
- The probe was not seen live: the AI Studio free-tier quota for the analyst model ran out during slice 3. Try "orders from Customers whose state is 'CA'" (thelook stores full state names) when quota allows.
- `.env` sets `MODEL_ANALYST_FALLBACK=gemini-3.5-flash-lite`; the fallback answered live when Flash hit its free-tier 429 (again in slice 5).
