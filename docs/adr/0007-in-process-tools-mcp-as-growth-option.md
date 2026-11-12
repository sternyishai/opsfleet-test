# Agent tools run in-process; MCP is a growth option, not part of the design

The agent's tools (`get_schema`, `search_golden_trios`, `run_sql`, `save_report`, `list_my_reports`, `get_my_report`, `plan_deletion`, `execute_deletion`, `draft_persona`, `publish_persona`, `rollback_persona`) are Python functions inside the Chat API. Each calls its dependencies through ports ([ADR 0006](0006-ports-and-adapters.md)). We do not run MCP servers.

Three reasons:
- **Swapping a data source is already cheap.** Moving from BigQuery to another warehouse is a new `QueryRunner` adapter plus the guard's SQL dialect setting, with no graph change. An MCP server would add a network hop, a service to deploy and secure, and cross-process trace propagation for the same result.
- **Safety stays simple.** These tools are safe because the User's identity (Author, Brands) is injected from graph state by code, never passed by the model. In-process, that is a state read. Behind MCP it becomes caller authentication plus User-token propagation, with the query guard re-enforced in another service.
- **There is only one consumer.** Nothing but our agent needs these tools today.

## Considered Options

- **All tools behind MCP**: standard tool discovery and independent deploys, but every safety guarantee moves across a network boundary for no current benefit.
- **One MCP server per data source** (BigQuery, app DB, Golden Bucket files): looks swappable, but ports already give that. The agent never reads Golden Bucket files at question time (Trios are queried from pgvector), so a file server would have no caller.
- **Off-the-shelf BigQuery MCP server**: rejected outright. It runs arbitrary SQL and would bypass the query guard and Brand scoping.

## Consequences: when we would adopt MCP

MCP is the named growth path, adopted only when one of these triggers occurs:

1. **Extension capabilities with existing MCP servers** (web search, email, Slack posting). A read capability such as web search would be loaded as a LangGraph tool through an MCP client adapter, with a per-server tool allowlist and tracing. An outbound one such as email or Slack posting would not become a tool the model can call: the MCP server would sit behind an adapter of the `Delivery` port, reached only from the `deliver` branch ([ADR 0009](0009-outbound-actions-are-a-deliver-branch.md)).
2. **A second consumer of governed data access** (another team's agent, or an enterprise assistant). `get_schema` and `run_sql` would move into a governed-data MCP server that enforces the query guard, Brand scope and PII rules itself, with User identity via OAuth. It would be the same code behind the same ports, hosted differently.

Until then, new capabilities are added as in-process tools behind ports.
