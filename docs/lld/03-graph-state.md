# 03 · Graph state

The LangGraph state is [`AgentState`](../../src/agent/graph/state.py): one per Conversation (`thread_id = conversation_id`), checkpointed after every node. The node names are in [`graph/build.py`](../../src/agent/graph/build.py) and are also the span names in traces. Edges follow the reference graph in [HLD 01 § Agent graph](../hld/01-architecture.md#agent-graph).

## Fields by lifetime

**Conversation fields** survive from turn to turn.

| Field | Type | Written by | Read by |
|---|---|---|---|
| `conversation_id`, `channel` | `UUID`, `Channel` | first turn | everything; `this_conversation` filters |
| `messages` | `list[AnyMessage]` with `add_messages` | every node that talks | analysis, answer writing |
| `running_summary` | `str` | `respond` when history exceeds `verbatim_turns` (`MODEL_SUMMARIZER`) | analysis |
| `access_version_seen` | `int` | `load_context` | `load_context`: if it differs from `user.access_version`, earlier results and the summary are dropped and the User is told |
| `open_plan` | `DeletionPlan \| None` | `plan_deletion` (replaces, so a new plan cancels the old one) | `confirm_deletion`, `execute_deletion` |
| `deferred_request` | `str \| None` | `router` | `after`: runs it through `retrieve_trios` once the delete or Persona change is answered |

**Turn fields** are reset by `load_context` at the start of every turn ([`TURN_KEYS`](../../src/agent/graph/state.py)).

| Group | Fields | Notes |
|---|---|---|
| Identity and trace | `turn_id`, `trace_id`, `user` | `user` is a frozen `UserContext`; nothing downstream can change it. `None` for an unknown or inactive User, whose turn goes straight to `refuse` with `NO_ROLE` (as does a known User with no role) |
| Presentation | `persona`, `profile` | `profile` is the stored profile with this turn's preferences merged in |
| Routing | `router` | the `RouterDecision` |
| Analysis | `trios`, `retrieval_degraded`, `analysis_steps`, `sql_attempts`, `sql_retries`, `empty_diagnoses`, `ran_sql`, `last_query_scope`, `result_summaries`, `saved_report_ids` | the retry counters are the `SqlBudget` that `run_sql` reads and returns; `ran_sql` with `trios == []` marks a retrieval gap |
| Deletion | `deletion_outcome` | |
| Persona | `persona_draft`, `persona_gate` | |
| Outcome | `error_class`, `message_class` | set by the graph-boundary catch-all or a refusal |

The HLD's scope list asks for "this turn's preference statements": they are `router.preferences`, and their effect is `profile`. Nothing stores them twice.

## What travels beside state

- **`TurnRuntime`** ([`state.py`](../../src/agent/graph/state.py)) is LangGraph's run `context`: the `user_id` the channel resolved and the turn's `Outbound`. `ChatService.handle` passes it to `invoke`; nodes read it from `Runtime`. It is never checkpointed.
- **The catch-all** is `ChatService.handle`, around `invoke`: an exception from any node ends the turn as its catalog message (`AgentError`) or `UNCLASSIFIED` with the reference ID. Nodes do not wrap themselves.
- **The checkpointer allowlist.** `CHECKPOINT_TYPES` lists every one of our types reachable from `AgentState`, derived from its annotations. The serializer is built with it, and tests run with `LANGGRAPH_STRICT_MSGPACK=true`, so a new state type that the checkpointer would refuse fails a test.

## What never enters state

- **Result rows.** `run_sql` returns rows to the model for one step only. State and the checkpoint keep a `ResultSummary` (columns, row count, key figures), so prompts stay small and checkpoints hold no bulk data ([01](../hld/01-architecture.md#conversation-state)). The `analysis` node keeps the ReAct loop's tool calls and results in its own message list and returns only the final answer and `ResultSummary.of(result)` (key figures for a one-row result, the shape otherwise).
- **Unfiltered outbound text.** The `output_filter` node writes the filtered reply into `messages`, so checkpoints hold what the User saw.
- **Ports and settings.** They reach tools through `TurnContext`, never through state, so they are not serialized.

## Pause and resume

`confirm_deletion` calls `interrupt()` with the `ConfirmationRequest`. The paused state, `open_plan` included, is checkpointed. The User's reply resumes the graph with `Command(resume=reply)`, and `parse_reply` ([`core/confirmation.py`](../../src/agent/core/confirmation.py)) reads it:

| Decision | Next node |
|---|---|
| `ConfirmAll`, `ConfirmExcept` | `execute_deletion` with the confirmed IDs |
| `Decline` | `after` |
| `NeedsCount` | `confirm_deletion` again, once, asking for the count |
| `NotAnAnswer` | the plan is cancelled (`open_plan = None`) and the reply re-enters at `input_guard` as a new message |

Everything a node does before `interrupt()` must be safe to run twice, because LangGraph re-runs the node from its start on resume. `plan_deletion` is a separate node for that reason: the plan is built once, and `confirm_deletion` only reads it.
