# 04 · Contracts: router, channel messages, Persona gate, errors

Every schema here is a Pydantic model in `src/agent/contracts/` or `src/agent/domain/`. Validators encode the rules, so an invalid shape fails at the seam, not three nodes later.

## Router output: `RouterDecision`

[`contracts/router.py`](../../src/agent/contracts/router.py). One Flash-Lite call (`MODEL_ROUTER`) with a locked prompt and structured output. Deterministic rules run first and can block without the call ([core/input_rules.py](../../src/agent/core/input_rules.py)): a length cap (`INPUT_MAX_CHARS`, LLD default 2000, below the channel's 4000), known injection phrasings and tags that close the router's `<message>` / `<conversation>` delimiters, and encoded payloads (base64 runs of 40+ characters, URL-encoded or escaped code points, invisible and bidi control characters). Each rule blocks as `injection`. A rule is a shape no analysis question has, so it never needs the model; a router failure ends the turn at the `ChatService` catch-all, so no analysis runs (fail closed).

| Field | Type | Rule (validator) |
|---|---|---|
| `verdict` | `allowed \| off_topic \| pii_request \| injection` | |
| `action` | `analysis \| delete_reports \| change_persona \| preference_only` | set exactly when `verdict = allowed` |
| `search_question` | `str` | set exactly for `analysis`; standalone, carries earlier context ([ADR 0008](../adr/0008-trio-retrieval-is-a-fixed-step.md)) |
| `persona_op` | `change \| undo` | set exactly for `change_persona`; picks the `draft_persona` or the `rollback_persona` edge |
| `preferences` | `list[PreferenceStatement]` | any action may carry them; `preference_only` needs at least one |
| `deferred_request` | `str` | only with `delete_reports` or `change_persona` |

`PreferenceStatement` ([domain/profile.py](../../src/agent/domain/profile.py)) is `scope` (`standing` or `this_turn`), `field` (`format`, `depth`, `charts`, `language` or `note`) and `value`.

**Not yet:** `deliver` as an action, `destination` and `follow_up_action` arrive with the first Delivery adapter ([ADR 0009](../adr/0009-outbound-actions-are-a-deliver-branch.md)).

**Per-step eval:** action accuracy, standalone-question quality on follow-ups, preference extraction, and block precision and recall ([07](../hld/07-quality-assurance.md)).

## Channel messages

[`contracts/channel.py`](../../src/agent/contracts/channel.py).

| Message | Direction | Purpose |
|---|---|---|
| `InboundMessage` | channel → Chat API | `user_id` (resolved by SSO or `--as`), `channel`, `conversation_id` (None starts one), `text` (≤ 4,000 chars) |
| `ConfirmationResponse` | channel → Chat API | Buttons and checkboxes in Slack, Teams and web: `request_id`, `approved`, `selected_indices`. The CLI sends text instead; both reach the same `ConfirmationDecision`. |
| `ProgressEvent` | Chat API → channel | A catalog template plus tool and table names, never free model text. Regex detectors only. |
| `ConfirmationRequest` | Chat API → channel | The Deletion Plan: the first 20 items, `total`, `require_count` for large plans |
| `AgentReply` | Chat API → channel | The turn's final message; `message_class` set when it is a catalog message; `data_as_of` |

The three outbound messages share `conversation_id`, `trace_id` (the reference ID) and `sent_at`, and form the discriminated union `OutboundMessage`. **They all leave through [`Outbound.emit`](../../src/agent/core/outbound.py)**, the single exit that runs the output PII filter ([ADR 0003](../adr/0003-layered-data-access-enforcement.md)).

## Confirmation grammar: `parse_reply`

[`core/confirmation.py`](../../src/agent/core/confirmation.py). Pure code, so an ambiguous reply can never delete.

| Reply | Plan | Decision |
|---|---|---|
| `yes` | ≤ 20 items | `ConfirmAll` |
| `yes` | > 20 items | `NeedsCount` (one re-prompt) |
| `yes, delete all 37` | 37 items | `ConfirmAll` |
| `yes except 2,3` | any | `ConfirmExcept([2, 3])`; indices outside the plan, or excluding every item → `NotAnAnswer` |
| `no` | any | `Decline` |
| anything else, or a button for another `request_id` | any | `NotAnAnswer` |

## Persona Quality Gate: `PersonaGateResult`

[`domain/persona.py`](../../src/agent/domain/persona.py), run by [`core/persona_gate.py`](../../src/agent/core/persona_gate.py).

- `passed: bool`, `checks: list[GateCheckResult]`, `could_not_run: bool`.
- Each `GateCheckResult` is the check (`FIELD_LIMITS`, `INJECTION_SCAN`, `SAFETY_HOLDS`, `SECTIONS_PRESENT`, `FIGURES_UNCHANGED`, in that order), `passed`, a plain-language `reason` with a hint, and a trace-only `detail`.
- Checks stop at the first failure, cheap code checks first.
- **Validators:** `passed` equals "every check passed", and a passing result has run all five. So a half-run gate cannot be stored as a pass, and `PersonaStore.publish` refuses anything that is not a pass.
- `could_not_run = true` is the fail-closed path (for example, the model is down): nothing is published, and the CEO gets `MODEL_BUSY`.

## Error model

[`errors.py`](../../src/agent/errors.py). Every port raises only these; provider exceptions never cross a seam.

| Exception | `ErrorClass` | Response | Examples |
|---|---|---|---|
| `SelfCorrectableError` | `self_correctable` | Back to the model inside `run_sql`, within the budget | syntax, schema, runtime error |
| `TransientError(dependency=…)` | `transient` | The adapter already retried and fell back; the graph shows the catalog message | Gemini down, BigQuery down, app DB down |
| `PermanentError` | `permanent` | Catalog message, no retry | quota reached |
| any other exception | `unclassified` | Graph-boundary catch-all: `UNCLASSIFIED` message with the reference ID, traced and alerted | |

`MessageClass` holds every catalog key, failures and refusals alike: `data_source_down`, `model_busy`, `self_correction_exhausted`, `query_too_large`, `out_of_scope`, `quota_reached`, `unavailable`, `unclassified`, `off_topic`, `pii_request`, `injection`, `no_role`, `persona_forbidden`, `persona_gate_failed`, `persona_nothing_to_roll_back`, `nothing_matched`, `plan_expired`, `report_not_found`, `report_out_of_scope`. The wording is in the Persona's `messages` field, with bundled defaults ([09](../hld/09-persona-management.md)).

Expected outcomes that are not failures (an empty result, a guard rejection the model can fix, zero matches) are **values** in the tool outputs ([05](05-tools.md)), not exceptions.
