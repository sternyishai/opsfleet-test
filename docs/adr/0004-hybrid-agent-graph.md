# Hybrid agent graph: deterministic shell, ReAct analysis core

The agent is a LangGraph graph with a deterministic outer shell and a ReAct sub-agent for analysis.

- **Outer shell** (fixed edges): load context → input guard and intent router → branch → output PII filter → respond. The `output_filter` node only keeps checkpoints clean, so history holds what the User saw; the guarantee is the single exit of [ADR 0003](0003-layered-data-access-enforcement.md), which also covers what leaves before the end of the graph.
- **Golden Trio retrieval** (`retrieve_trios`): a fixed node on the analysis branch, before the sub-agent, because Req 1 makes it a step that must happen ([ADR 0008](0008-trio-retrieval-is-a-fixed-step.md)).
- **Analysis sub-agent** (ReAct loop) with the tools `get_schema`, `search_golden_trios`, `run_sql`, `save_report`, `list_my_reports` and `get_my_report`. The report tools take no author parameter; the Author is injected from graph state. The query guard and SQL self-correction live inside `run_sql`. The loop is written inside the `analysis` node rather than built with LangChain's `create_agent`; why is in `docs/decision-log.md` § Analysis loop.
- **Report deletion**: its own deterministic branch, with an `interrupt()` for confirmation.
- **Persona change**: its own deterministic branch, open only to the `persona_editor` permission, with an automated quality gate before the new version goes live (see `docs/hld/09`).

The rule for what becomes a branch rather than a tool: an action that destroys data, changes behavior for every User, or sends data outside the system is a fixed branch that only the router can open from the User's own message. The router decides on the User's new message; the last few turns are delimited context only, and it never sees raw query results or Trios. The analysis model, which does read them, can never call a branch. Earlier answers can still quote result text, which is one more reason the branches carry their own checks: deletion (and `deliver`, [ADR 0009](0009-outbound-actions-are-a-deliver-branch.md)) stop at a confirmation `interrupt()` only the User can answer, and a Persona change needs the `persona_editor` permission and passes the quality gate. A misroute fails safe: a delete request routed to analysis finds no delete tool, and analysis routed to deletion still stops at the confirmation.

Safety steps, and steps a requirement makes mandatory, are structural, so the model cannot skip them. Analysis stays flexible for multi-step "why" questions. New read capabilities that the model chooses mid-analysis (charts, web search) are added as new tools without touching the shell. Outbound capabilities (email, Slack, push) are adapters of the `deliver` branch ([ADR 0009](0009-outbound-actions-are-a-deliver-branch.md)).

## Considered Options

- **Single ReAct agent**: the fastest to build and the most flexible, but guardrails and PII filtering would depend on the model choosing to call them.
- **Fixed pipeline**: predictable and cheap, but cannot handle multi-step questions that need several queries.

## Consequences: how we would validate this in a real engagement

We built the hybrid directly. The safety requirements already rule out a single ReAct agent: confirmation (Req 3) and PII filtering cannot depend on the model choosing them. In a real engagement the path would still be earned stage by stage on the offline eval set (answer correctness, safety pass rate, cost and latency per question):

1. **PoC: one tool-using agent, demoed to stakeholders.** Read-only questions only, with the read-only service account and the query guard inside `run_sql`, and no destructive actions. Proves that the tools, schema context and Gemini can answer the spec's example questions, and sets the baseline eval scores. Not multi-agent: nothing in this problem needs it.
2. **Pipeline experiment.** Fix the common path as deterministic steps and measure the cost and latency gain on simple questions, and the correctness loss on multi-step ones.
3. **Hybrid (this decision).** Move safety into the fixed shell and keep ReAct for analysis. The eval must show safety tests at 100% with no correctness regression from stage 1.
4. **Production hardening.** Add gradual rollout (`docs/hld/11`) and online metrics (`docs/hld/08`).

Stages 1 and 2 were not run for this design; the eval harness can run a single-agent baseline if a number is wanted.
