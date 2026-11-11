# Hybrid agent graph: deterministic shell, ReAct analysis core

The agent is a LangGraph graph with a deterministic outer shell and a ReAct sub-agent for analysis.

- **Outer shell** (fixed edges): input guard and intent router → branch → output PII filter → respond.
- **Analysis sub-agent** (ReAct loop) with the tools `get_schema`, `search_golden_trios`, `run_sql` and `save_report`. The query guard and SQL self-correction live inside `run_sql`.
- **Report deletion**: its own deterministic branch, with an `interrupt()` for confirmation.

Safety steps are structural, so the model cannot skip them. Analysis stays flexible for multi-step "why" questions. New capabilities (charts, email, web search) are added as new tools without touching the shell.

## Considered Options

- **Single ReAct agent**: the fastest to build and the most flexible, but guardrails and PII filtering would depend on the model choosing to call them.
- **Fixed pipeline**: predictable and cheap, but cannot handle multi-step questions that need several queries.

## Consequences: evolution path from PoC to production

Each stage is promoted only when it beats the previous one on the offline eval set (answer correctness, safety pass rate, cost and latency per question):

1. **PoC: single ReAct agent.** Proves that the tools, schema context and Gemini can answer the spec's example questions, and establishes the baseline eval scores.
2. **Pipeline experiment.** Fix the common path as deterministic steps and measure the cost and latency gain on simple questions, and the correctness loss on multi-step ones.
3. **Hybrid (this decision).** Move safety into the fixed shell and keep ReAct for analysis. The eval must show safety tests at 100% with no correctness regression from stage 1.
4. **Production hardening.** Add shadow traffic, canary rollout behind config flags, and online metrics (see Observability).
