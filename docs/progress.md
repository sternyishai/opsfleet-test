# Progress

The session-to-session handoff. Updated at the end of every session (see [work-plan.md](work-plan.md#how-context-moves-between-sessions)). Newest state at the top; history kept short.

## Current state (2026-11-14)

**Phase:** 2 (LLD), starting. Phase 1 is merged (PR #2).
**Branch:** `docs/lld`

| Step | Status |
|---|---|
| Phase 0: design foundation | ✅ merged (PR #1) |
| Phase 1: HLD `00`–`12`, ADRs 0001–0009, diagrams | ✅ merged (PR #2) |
| External design review | ✅ fixed in 2ae9e0f: Persona gate check 3 tests the answer-writing step; running turns are not resumed after a restart; service account + query guard is the BigQuery baseline (per-User OAuth is an option); Saved Report `scope` re-checked on use; Trios keep method parameters; hard delete defined (Q17); Pub/Sub pushes to a worker service; Deletion Plan bound to User and latest plan; Trio Candidates off until Q1 |
| `/mock-interview interview` rehearsal | deferred until the prototype runs, so answers can point to working code |
| Phase 2: LLD | not started |

## Decisions made this session, not yet written elsewhere

- None. The review's changes are in the HLD, ADRs 0003 and 0005, and the decision log.

## Open questions

- 17 client questions (Q1–Q17) in `docs/hld/12`; each has a default, so none blocks Phase 2.
- Not decided in the design: what triggers the Trio Candidate job, and whether an approved Candidate goes through ingestion. Candidate generation is off by default until Q1 is answered, so this does not block the prototype.
- Cost estimate inputs in `11` are list prices; recompute from prototype traces in Phase 4.

## Next session prompt

> Read docs/hld/, docs/adr/, docs/lld/README.md and docs/work-plan.md. Build the code-first LLD: a `uv` project skeleton, Pydantic schemas, `Protocol` ports, SQL DDL and an ER diagram in `src/`, with explanations in `docs/lld/`. Use the codebase-design skill. Stubs only, no implementation.
