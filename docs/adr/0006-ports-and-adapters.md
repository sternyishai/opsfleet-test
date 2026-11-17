# Ports and adapters for every external dependency

Every external dependency sits behind a Python `Protocol` (a port), with one concrete adapter per technology, and everything is wired in a single composition root. The dependencies are the LLM providers, BigQuery, the app-state database, the Golden Bucket store, the observability backend and the channels. This is what makes three of our claims true in code rather than only in documents: the system moves between clouds, models and providers can be swapped per step, and new capabilities are easy to add. Moving from SQLite to Cloud SQL, or from the public dataset to our own row-level-secured copy, is a new adapter plus one wiring change, with no change to the agent graph. It also lets the TDD suites test the graph and the safety layers against fakes, without network access or cost.

`LLMFactory` returns LangChain chat models, so LangChain already abstracts the provider. The port adds what LangChain does not: one model setting and one fallback per step (`ModelStep`), so each call can be changed and evaluated on its own.

## Considered Options

- **Call SDKs directly from the graph and tools**: the least code, but the safety layers could only be tested against live BigQuery and Gemini, and every provider change would touch the graph. Rejected.
- **A dependency-injection framework** (for example `dependency-injector`): container wiring for a graph that has one composition root. Rejected.

## Consequences

- The graph and tools depend only on ports, never on SDKs directly. This is enforced, not left to review: ruff's `banned-api` rejects provider SDKs and `agent.adapters` outside the adapters, the composition root and the tests (`pyproject.toml`), and `tests/test_layers.py` keeps the packages in their dependency order.
- The list of ports is kept in [LLD 06](../lld/06-ports.md), with each port's methods and adapters, so this ADR does not repeat it.
