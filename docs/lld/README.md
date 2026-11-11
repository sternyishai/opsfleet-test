# Low-Level Design

> **This is a living document.** The LLD is iterated continuously. Whenever a schema, contract or interface changes in code, the matching section here (and its diagram) is updated in the same change. When a new tool or port is added, it gets its schema entry here before it is implemented.

The schemas and interfaces are **code-first**: Pydantic models, Python `Protocol`s and SQL DDL live in `src/`, and this folder explains them and links to them. If this folder and the code disagree, the code wins, and this folder must be fixed.

## Scope

1. **Data schemas**: application tables and an ER diagram.
   `users`, `brand_assignments`, `conversations`, `saved_reports`, `report_entities`, `deletion_audit`, `user_profiles`, `golden_trios`, `persona_versions`, `interactions`.
2. **Graph state**: the LangGraph `State`: messages, User context (role, Brands), intent, retry counters, pending Deletion Plan, trace/reference ID.
3. **Contracts**: a typed input and output schema for **every tool** and every channel message.
   - Tools: `get_schema`, `search_golden_trios`, `run_sql`, `save_report`, `plan_deletion`, `execute_deletion`.
   - Channel messages: `InboundMessage`, `AgentReply`, `ConfirmationRequest`, `ConfirmationResponse`.
4. **Interfaces (ports)**, see ADR 0006: `LLMFactory`, `QueryRunner`, `QueryGuard`, `PiiFilter`, `ReportRepository`, `TrioRetriever`, `ProfileStore`, `PersonaStore`, `ChannelAdapter`, `Tracer`.

## Checklist for each tool or port

- [ ] Input and output schema (Pydantic), including error variants
- [ ] Which ports it depends on
- [ ] Safety rules it enforces or relies on
- [ ] Failure modes and the user-facing message class for each
- [ ] Trace attributes it emits
- [ ] Test cases (link to tests)
