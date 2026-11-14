# Progress

The session-to-session handoff. Updated at the end of every session (see [work-plan.md](work-plan.md#how-context-moves-between-sessions)). Newest state at the top; history kept short.

## Current state (2026-11-14)

**Phase:** 2 (LLD) merged (PR #3); eval LLD in PR #4. Next: Phase 3 (GitHub issues) once PR #4 is merged.
**Branch:** `docs/lld`

| Step | Status |
|---|---|
| Phase 0: design foundation | ✅ merged (PR #1) |
| Phase 1: HLD `00`–`12`, ADRs 0001–0009, diagrams | ✅ merged (PR #2) |
| External design review | ✅ fixed in 2ae9e0f: Persona gate check 3 tests the answer-writing step; running turns are not resumed after a restart; service account + query guard is the BigQuery baseline (per-User OAuth is an option); Saved Report `scope` re-checked on use; Trios keep method parameters; hard delete defined (Q17); Pub/Sub pushes to a worker service; Deletion Plan bound to User and latest plan; Trio Candidates off until Q1 |
| `/mock-interview interview` rehearsal | deferred until the prototype runs, so answers can point to working code |
| Phase 2: LLD | ✅ committed (165e81d … b0bbc30): `uv` skeleton, Pydantic contracts, 12 `Protocol` ports, graph state, stubbed tools and core modules, Postgres + SQLite DDL (Postgres verified in `pgvector/pg17`), ER diagram, `docs/lld/01`–`07`, 12 passing tests |
| One model setting per call | ✅ 12 `ModelStep`s with inheritance; HLD 10 table and 02 updated; settles the figure-stripping model (`MODEL_TRIO_INGEST`, Flash) |
| ADR 0010: UI in a plain `web/` folder | ✅ optional slice 8 in the work plan; CLI and UI share `ChatService` and `Views` (LLD 07) |
| Eval LLD + real seed data (PR #4) | ✅ LLD 08, `evals/` (20 cases, pre-registered bar), `seed/users.yaml`, 5 seed Trios; all queries verified on BigQuery via project `opsfleet-prototype` (ADC set up) |

## Decisions made this session, not yet written elsewhere

- None. This session's approved additions are in `decision-log.md` § LLD (Phase 2).

## Open questions

- 17 client questions (Q1–Q17) in `docs/hld/12`; each has a default, so none blocks Phase 3.
- Not decided in the design: what triggers the Trio Candidate job, and whether an approved Candidate goes through ingestion. Candidate generation is off by default until Q1 is answered, so this does not block the prototype.
- Cost estimate inputs in `11` are list prices; recompute from prototype traces in Phase 4.
- The "LLD default" limits in `config.py` are starting values; tune them from traces in Phase 4.

## Next session prompt

> Read docs/hld/, docs/lld/ and docs/work-plan.md. Create GitHub issues as vertical slices following docs/agents/issue-tracker.md, labeled `ready-for-agent`, each one referencing the ports and schemas it implements and including acceptance criteria. Use the slice order in docs/work-plan.md (slice 8, the web UI, is optional).
