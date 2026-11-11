# Ports and adapters for every external dependency

Every external dependency sits behind a Python `Protocol` (a port), with one concrete adapter per technology, and everything is wired in a single composition root. The dependencies are the LLM providers, BigQuery, the app-state database, the Golden Bucket store, the observability backend and the channels. This is what makes three of our claims true in code rather than only in documents: the system moves between clouds, models and providers can be swapped per step, and new capabilities are easy to add. Moving from SQLite to Cloud SQL, or from the public dataset to our own row-level-secured copy, is a new adapter plus one wiring change, with no change to the agent graph. It also lets the TDD suites test the graph and the safety layers against fakes, without network access or cost.

## Consequences

- The graph and tools depend only on ports, never on SDKs directly. An import of `google.cloud` or a provider SDK outside an adapter is a review smell.
- The ports are: `LLMFactory`, `QueryRunner`, `QueryGuard`, `PiiFilter`, `ReportRepository`, `TrioRetriever`, `ProfileStore`, `PersonaStore`, `ChannelAdapter`, `Tracer`. This list is maintained in `docs/lld/`.
