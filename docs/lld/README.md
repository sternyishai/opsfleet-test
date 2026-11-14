# Low-Level Design

> **This is a living document.** The LLD is iterated continuously. Whenever a schema, contract or interface changes in code, the matching section here (and its diagram) is updated in the same change. When a new tool or port is added, it gets its schema entry here before it is implemented.

The schemas and interfaces are **code-first**: Pydantic models, Python `Protocol`s and SQL DDL live in `src/`, and this folder explains them and links to them. If this folder and the code disagree, the code wins, and this folder must be fixed.

## Contents

| File | Covers | Code |
|---|---|---|
| [01-modules](01-modules.md) | Package map, dependency rule, where the seams are and why | [`src/agent/`](../../src/agent/) |
| [02-data](02-data.md) | Application tables, DDL decisions, prototype differences | [`db/postgres.sql`](../../src/agent/db/postgres.sql), [`db/sqlite.sql`](../../src/agent/db/sqlite.sql), [`db/ER.md`](../../src/agent/db/ER.md) |
| [03-graph-state](03-graph-state.md) | `AgentState` by lifetime, pause and resume | [`graph/state.py`](../../src/agent/graph/state.py), [`graph/build.py`](../../src/agent/graph/build.py) |
| [04-contracts](04-contracts.md) | `RouterDecision`, channel messages, confirmation grammar, `PersonaGateResult`, error model | [`contracts/`](../../src/agent/contracts/), [`errors.py`](../../src/agent/errors.py) |
| [05-tools](05-tools.md) | The eleven tools, checklist filled in per tool | [`contracts/tools.py`](../../src/agent/contracts/tools.py), [`tools/`](../../src/agent/tools/) |
| [06-ports](06-ports.md) | The twelve ports, checklist filled in per port, trace attribute names | [`ports/`](../../src/agent/ports/) |
| [07-web](07-web.md) | One core for the CLI and the optional web UI: `ChatService`, `Views`, the feature map, SSE, identity, generated types ([ADR 0010](../adr/0010-ui-is-a-plain-folder-with-generated-types.md)) | [`core/chat.py`](../../src/agent/core/chat.py), [`core/views.py`](../../src/agent/core/views.py), [`contracts/web.py`](../../src/agent/contracts/web.py) |

Everything in `src/` is a stub: schemas, Protocols and signatures, with bodies that raise `NotImplementedError`. `uv run pytest` checks that every module imports, that both DDL files agree, and the validators that encode the rules (scope, router shape, deletion filter, gate result, no identity in tool inputs).

## Scope

1. **Data schemas**: application tables and an ER diagram.
   `users`, `user_permissions`, `brand_assignments`, `conversations`, `saved_reports`, `report_entities`, `deletion_audit`, `user_profiles`, `golden_trios`, `persona_versions`, `interactions`.
2. **Graph state**: the LangGraph `State`: messages, User context (role, permissions, Brands), intent (action), this turn's preference statements, retry counters, pending Deletion Plan, Persona draft and gate result, deferred request, trace/reference ID.
3. **Contracts**: a typed input and output schema for **every tool** and every channel message.
   - Tools: `get_schema`, `search_golden_trios`, `run_sql`, `save_report`, `list_my_reports`, `get_my_report`, `plan_deletion`, `execute_deletion`, `draft_persona`, `publish_persona`, `rollback_persona`.
   - Router output: `RouterDecision` (action: analysis, delete reports, change Persona or preference only; plus preference statements, each standing or this-turn).
   - Persona Quality Gate: `PersonaGateResult` (pass or fail, the result and reason of each check).
   - Channel messages: `InboundMessage`, `AgentReply`, `ConfirmationRequest`, `ConfirmationResponse`; web bodies `WebText`, `WebConfirmation` ([07](07-web.md)).
4. **Interfaces (ports)**, see ADR 0006: `LLMFactory`, `QueryRunner`, `QueryGuard`, `PiiFilter`, `ReportRepository`, `TrioRetriever`, `ProfileStore`, `PersonaStore`, `ChannelAdapter`, `Tracer`; added in the LLD: `UserDirectory`, and `Delivery` (declared, not wired, [ADR 0009](../adr/0009-outbound-actions-are-a-deliver-branch.md)).
5. **Tables beyond the list above**: `trio_embeddings` (one vector per question and paraphrase) and `persona_events` (publish, rejection and rollback records). See [02](02-data.md).

## Checklist for each tool or port

- [ ] Input and output schema (Pydantic), including error variants
- [ ] Which ports it depends on
- [ ] Safety rules it enforces or relies on
- [ ] Failure modes and the user-facing message class for each
- [ ] Trace attributes it emits
- [ ] Test cases (link to tests)

Status: every item is filled in for all eleven tools ([05](05-tools.md)) and all twelve ports ([06](06-ports.md)). Test cases are named with their target file and slice; the files themselves are written test-first in Phase 4.
