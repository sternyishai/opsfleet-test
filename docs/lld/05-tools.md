# 05 · Tools

Eleven tool functions in [`src/agent/tools/`](../../src/agent/tools/), with their schemas in [`contracts/tools.py`](../../src/agent/contracts/tools.py).

- **Analysis tools** ([`analysis.py`](../../src/agent/tools/analysis.py)) are bound to the ReAct sub-agent: `get_schema`, `search_golden_trios`, `run_sql`, `save_report`, `list_my_reports`, `get_my_report` (`ANALYSIS_TOOLS` in [`graph/build.py`](../../src/agent/graph/build.py)).
- **Branch functions** ([`branches.py`](../../src/agent/tools/branches.py)) run only inside router-opened fixed branches and are never offered to a model: `plan_deletion`, `execute_deletion`, `draft_persona`, `publish_persona`, `rollback_persona` ([ADR 0004](../adr/0004-hybrid-agent-graph.md)).

## Shared rules

- **Shape.** Every tool is a plain function `(Input, TurnContext[, state values]) -> Output`. The graph wraps analysis tools as LangChain tools whose schema is only the `*Input` model; `TurnContext` and state values are injected. Tests call the functions directly with fake ports.
- **Identity is injected.** No input names a User, Author or Brand scope. A test enforces it ([01](01-modules.md#identity-never-crosses-the-model)).
- **Outputs are discriminated unions on `status`.** An expected outcome (rejected SQL, an empty result, nothing matched) is a value. Exceptions are only the classified errors that end the step ([04](04-contracts.md#error-model)).
- **Tool results are data.** They enter the model's context in a delimited block, never as instructions, and they can never route: only the router opens a branch.
- **Every call is one span** named after the tool, with the attributes listed below.

The checklist from the [README](README.md#checklist-for-each-tool-or-port) is filled in per tool. "Tests" names the cases the slice writes first (TDD) and the file they go in.

---

### `get_schema`

| | |
|---|---|
| **Schema** | `GetSchemaInput(tables?)` → `SchemaOutput(tables: [TableSchema], scope_note, data_as_of)` |
| **Ports** | none. Column descriptions are bundled with the guard's allowlist in [`domain/warehouse.py`](../../src/agent/domain/warehouse.py) (`ALLOWLIST`), so the two cannot disagree |
| **Safety** | Shows only allowlisted, non-PII columns, as *this User's* scoped view projects them: no `orders.num_of_item` for a Manager |
| **Failures** | none expected |
| **Trace** | `tables`, `role` |
| **Tests** · slice 1–2 · `tests/test_tools_schema.py` | no PII column in any output; Manager view lacks `num_of_item`; Executive view has it |

### `search_golden_trios`

| | |
|---|---|
| **Schema** | `SearchTriosInput(query, limit=3)` → `SearchTriosOutput(matches: [TrioMatch])` |
| **Ports** | `TrioRetriever` |
| **Safety** | Scope from `ctx.user.scope`; only `active` Trios fully inside the scope; figure-stripped content ([ADR 0005](../adr/0005-golden-trios-teach-method-not-figures.md)) |
| **Failures** | `TransientError` → empty matches; the step continues |
| **Trace** | `query`, returned `trio_id@version` and scores, `dropped_below_threshold` |
| **Tests** · slice 7 · `tests/test_trios.py` | Manager never gets an out-of-scope or all-Brand Trio; below-threshold dropped; retriever down → empty, no error |

### `run_sql`

| | |
|---|---|
| **Schema** | `RunSqlInput(sql)` → `RunSqlOk(result, attempt)` \| `RunSqlRetry(kind, reason?, error, hint?, relevant_schema, retries_left)` \| `RunSqlEmpty(probe_hint?, checked, final)` \| `RunSqlGaveUp(tried)` \| `ToolError`; plus the next `SqlBudget` |
| **Ports** | `QueryGuard`, `QueryRunner` |
| **Safety** | Nothing reaches BigQuery without `QueryGuard.check`; probes pass the same guard; Brands bound as parameters; forced `LIMIT`; dry run then `maximum_bytes_billed` ([03](../hld/03-safety-and-pii.md#query-guard-the-core-guarantee)) |
| **Failures** | guard rejection, dry-run error, bytes cap, runtime error → `retry` (shared budget of 2) → `gave_up` (`SELF_CORRECTION_EXHAUSTED`). Out-of-scope Brand → `retry` with the Brands the User *can* analyze; if the model insists, the turn ends with `OUT_OF_SCOPE`. 0 rows → `empty` with a probe hint, once; then `final`. BigQuery down → `TransientError` (`DATA_SOURCE_DOWN`). |
| **Trace** | `sql.model`, `sql.rewritten`, `guard.*` on a `query_guard` span ([06](06-ports.md#queryguard)), `dry_run.bytes`, `bytes_billed`, `row_count`, `attempt`, `error`, `probe.findings` |
| **Tests** · slices 1–3 · `tests/test_query_guard.py`, `tests/test_run_sql.py` | the safety suite (single SELECT, allowlist, PII columns, out-of-scope Brand, `num_of_item`, scope rewrite per role, parameter binding, forced LIMIT); syntax error then fix; wrong literal → probe hint → success; bytes cap; budget exhausted → graceful; BigQuery down |

### `save_report`

| | |
|---|---|
| **Schema** | `SaveReportInput(title, question, sql, markdown, structured)` → `SaveReportOk(report_id, created, redactions)` \| `ToolError` |
| **Ports** | `PiiFilter`, `ReportRepository`, `LLMFactory` (`ENTITY_EXTRACTION`, at save time in the prototype) |
| **Safety** | Author = `ctx.user.user_id`; scope = `SqlBudget.last_scope` from the guard, never from the model; every text field passes the PII filter before writing; only on the User's explicit request (prompt contract) |
| **Failures** | no successful `run_sql` this turn → `ToolError`; app DB down → `TransientError` (`UNAVAILABLE`); entity extraction fails → saved without entities (full-text fallback covers matching) |
| **Trace** | `report_id`, `created`, `redactions` (count and types), `entities` count |
| **Tests** · slice 4 · `tests/test_reports.py` | idempotent on (Conversation, turn); scope copied from the guard; PII in markdown is redacted before the write |

### `list_my_reports`

| | |
|---|---|
| **Schema** | `ListReportsInput(text?, entities, created?, this_conversation, limit)` → `ListReportsOutput(reports: [ReportListItem], total)` |
| **Ports** | `ReportRepository` |
| **Safety** | Author injected; `accessible = user.scope.covers(report.scope)`, and inaccessible reports are listed by title only |
| **Failures** | app DB down → `TransientError` (`UNAVAILABLE`) |
| **Trace** | `filter`, `total`, `inaccessible` count |
| **Tests** · slice 4 · `tests/test_reports.py` | never another Author's report; report outside current scope marked inaccessible |

### `get_my_report`

| | |
|---|---|
| **Schema** | `GetReportInput(report_id)` → `GetReportOk(report, label)` \| `ToolError(REPORT_NOT_FOUND \| REPORT_OUT_OF_SCOPE)` |
| **Ports** | `ReportRepository` |
| **Safety** | Another Author's report is `REPORT_NOT_FOUND`, never "forbidden", so existence is not revealed; content withheld when out of scope; labelled "data as of <date>" |
| **Failures** | as above; app DB down → `TransientError` |
| **Trace** | `report_id`, `found`, `accessible` |
| **Tests** · slice 4 · `tests/test_reports.py` | foreign ID → not found; out-of-scope → title only; label present |

### `plan_deletion` (branch)

| | |
|---|---|
| **Schema** | `PlanDeletionInput(filter: DeletionFilter)` → `PlanDeletionOk(plan)` \| `PlanDeletionNothing` |
| **Ports** | `ReportRepository.match`; `LLMFactory` (`DELETION_FILTER`) for the structured `DeletionFilter`, called by the node before this function |
| **Safety** | Author and Conversation injected; the model produces only the filter, code picks the reports; the plan stores IDs, not the filter, so later reports are never included; a new plan replaces `open_plan` |
| **Failures** | 0 matches → `nothing_matched` (`NOTHING_MATCHED`, no prompt); app DB down → `TransientError` |
| **Trace** | `filter`, `plan_id`, `plan.size` |
| **Tests** · slice 4 · `tests/test_deletion.py` | exact Conversation filter; entity match (case and alias); full-text fallback; Author isolation; zero matches |

### `execute_deletion` (branch)

| | |
|---|---|
| **Schema** | `ExecuteDeletionInput(plan_id, confirmed_report_ids)` → `ExecuteDeletionOk(outcome, replayed)` \| `ToolError(PLAN_EXPIRED)` |
| **Ports** | `ReportRepository.delete_with_audit` |
| **Safety** | Only for the `open_plan` of this User and Conversation; every confirmed ID must be in the plan; ownership re-checked inside the transaction; delete + entity cascade + audit in one transaction |
| **Failures** | stale, cancelled or foreign plan → `PLAN_EXPIRED`; replay → `replayed = true`, nothing deleted; report already gone → listed in `already_gone`; app DB down → `TransientError`, nothing deleted |
| **Trace** | `plan_id`, `outcome` (`confirmed`, `partial`, `cancelled`, `expired`), `deleted`, `kept`, `already_gone` counts |
| **Tests** · slice 4 · `tests/test_deletion.py` | partial confirmation; idempotent replay; expired plan; already-gone report; audit row has no content |

### `draft_persona` (branch)

| | |
|---|---|
| **Schema** | `DraftPersonaInput(request)` + current `PersonaVersion` → `PersonaDraft(persona, changed_fields, summary)` |
| **Ports** | `LLMFactory` (`PERSONA_DRAFTER`, structured output into `Persona`) |
| **Safety** | Reached only after `persona_editor` is checked in code; the draft is untrusted until the gate passes |
| **Failures** | model down → `TransientError` (`MODEL_BUSY`); nothing changes |
| **Trace** | `changed_fields` |
| **Tests** · slice 7 · `tests/test_persona.py` | only the fields the request names change (fake model) |

### `publish_persona` (branch)

| | |
|---|---|
| **Schema** | `PublishPersonaInput(persona, path)` → `PersonaPublished(active, previous_version, gate)` \| `PersonaRejected(gate, current_version, message_class)` |
| **Ports** | `PersonaQualityGate` (uses `LLMFactory` with `PERSONA_GATE`, `PiiFilter`), `PersonaStore` |
| **Safety** | Gate before publish; `PersonaStore.publish` refuses a non-passing result; fail closed when the gate cannot run |
| **Failures** | a check fails → `rejected` (`PERSONA_GATE_FAILED`, the reason shown); gate cannot run → `rejected` (`MODEL_BUSY`) |
| **Trace** | `path`, `gate.<check>` results, `version.before`, `version.after` |
| **Tests** · slice 7 · `tests/test_persona.py` | each check failing on its own; fail closed on model outage; rejection recorded, no version created |

### `rollback_persona` (branch)

| | |
|---|---|
| **Schema** | `RollbackPersonaInput(to_version?)` → `PersonaRolledBack(active, from_version)` \| `ToolError(PERSONA_NOTHING_TO_ROLL_BACK)` |
| **Ports** | `PersonaStore` |
| **Safety** | `persona_editor` checked first; no gate (the version already passed it) |
| **Failures** | version 1 active → `PERSONA_NOTHING_TO_ROLL_BACK` |
| **Trace** | `version.before`, `rollback.target` |
| **Tests** · slice 7 · `tests/test_persona.py` | rollback to previous; rollback at v1; Manager refused before any call |
