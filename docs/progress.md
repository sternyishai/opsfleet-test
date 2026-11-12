# Progress

The session-to-session handoff. Updated at the end of every session (see [work-plan.md](work-plan.md#how-context-moves-between-sessions)). Newest state at the top; history kept short.

## Current state (2026-11-12)

**Phase:** 1 (HLD), sessions 1A, 1B and 1C done; 1D next (files 11–12)
**Branch:** `docs/hld`

| Step | Status |
|---|---|
| Phase 0: design foundation | ✅ merged (PR #1) |
| `docs/work-plan.md` | ✅ committed |
| `docs/hld/00-overview.md` | ✅ committed (3c40858); light iteration later |
| `docs/hld/01-architecture.md` | ✅ committed (3c40858) |
| `docs/hld/diagrams/architecture.excalidraw` + `.png` | ✅ committed (3c40858); light iteration later |
| ADR 0007 (tools in-process, MCP as growth option) | ✅ committed (bb5fa72) |
| `docs/hld/02`–`05` | ✅ committed (session 1B) |
| `docs/hld/06-resilience.md` | ✅ committed (f7839e9) |
| `docs/hld/07-quality-assurance.md` | ✅ committed (93ff615) |
| `docs/hld/08-observability.md` | ✅ committed (eaa8083) |
| `docs/hld/09-persona-management.md` | ✅ committed (f9c7084) |
| `docs/hld/10-stack-rationale.md` | ✅ committed; **author's-experience rows still blank** (see open questions) |
| `docs/hld/11-operations.md` | not started (1D) |
| `docs/hld/12-assumptions-and-open-questions.md` | not started (1D) |

## Decisions made this session, not yet written elsewhere

- None. Every approved addition from 06–10 is folded into `decision-log.md` (look for the "Detail (in `docs/hld/0N-…`)" bullets).
- Session 1C was cut after file 10 to keep context small; `work-plan.md` now has a session 1D for 11–12.

## Open questions

- **10: author's experience.** Six rows are `_to fill in_`: Gemini/Vertex, BigQuery, GCP (Cloud Run, Pub/Sub, Cloud SQL), Postgres + pgvector, sqlglot, Phoenix/Langfuse. Only LangGraph is recorded (ADR 0001: hands-on; none with ADK/PydanticAI). Also confirm the framing sentence "where experience was lacking, the simpler option was preferred".
- 01 handoff: retention assumptions (checkpoints 90 days, deletion audit indefinite) go to `12`.

## Inputs for 12 raised in 06–10 (so 1D need not hunt)

- 06: per-User daily quota resets at midnight; Cloud SQL runs in HA mode (11 must describe it); fallback to a second Vertex region and a second provider assumes contracts/keys exist.
- 07: an executive pilot of 5–8 executives and Managers before launch (client question: can we get them?); a held-out share of Golden Trios needs enough Trios to exist; human-labeled answers to calibrate the judge (who labels?).
- 08: alert routing assumes an AI-ops team owns the agent, with business-hours paging except for PII and scope alerts (who is on call?).
- 09: who holds the Persona-editor role (CEO's office? a named editor?); scheduled weekly publishing.
- 10: Users have Google identities via SSO (already in open-items); Vertex AI as production model endpoint.

## Next session prompt

> Continue the OpsFleet take-home, Phase 1, session 1D. Read docs/work-plan.md, docs/progress.md, GLOSSARY.md, the decision log (Production concerns, Scale & SLOs, Packaging, Third-party resilience, Observability sections, and skim the rest for assumptions), docs/open-items.md, and docs/hld/00–10. First fill the author's-experience rows in 10 with the user. Then draft docs/hld/11-operations.md (deployment, scale, SLOs, back-of-envelope cost estimate, cost controls, security), then docs/hld/12-assumptions-and-open-questions.md, following the review loop: one file at a time, validate Mermaid with `npx @mermaid-js/mermaid-cli`, give the handoff note (sources + additions not in the sources), commit on approval and fold the approved additions into the decision log. File 12 must collect every assumption and client question raised in 00–11 (use "Inputs for 12" in progress.md plus open-items.md). Then wrap up Phase 1: check the work-plan "Done when" list and open PR #2.
