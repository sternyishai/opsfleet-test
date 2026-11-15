# The query guard parses SQL with sqlglot, and BigQuery's dry run validates it

The query guard (layer 2 of [ADR 0003](0003-layered-data-access-enforcement.md)) parses every SQL statement the model writes with **sqlglot**, always with the BigQuery dialect. It checks the syntax tree and rewrites it: one `SELECT`, allowlisted tables and columns, each table replaced by a Brand-scoped subquery, a forced `LIMIT` ([03](../hld/03-safety-and-pii.md#query-guard-the-core-guarantee)). The rewritten statement then goes to a **BigQuery dry run**, which validates it against the real schema, and only then runs with `maximum_bytes_billed`.

Four reasons for sqlglot:
- **The guard has to rewrite, not just check.** sqlglot gives a syntax tree that code can transform and turn back into SQL ("Parsed expressions can also be transformed recursively", [README](https://github.com/tobymao/sqlglot)). Scope injection is a tree edit, never string surgery.
- **BigQuery is a supported dialect**, listed as "Official" among more than 30. A warehouse change is a dialect setting plus a run of the safety suite, as [ADR 0007](0007-in-process-tools-mcp-as-growth-option.md) assumes.
- **It runs in-process.** It is "a no-dependency SQL parser" written "purely in Python", so the guard is a pure function inside the Chat API: no service and no network call, and the prototype installs it with `uv sync`.
- **It fails loudly.** Syntax errors raise `ParseError`, which the guard turns into a rejection, so it fails closed.

**What sqlglot is not.** Its README says so plainly: "The parser is intentionally lenient, so it can accept queries that a real engine would reject", and "SQLGlot is a transpiler, not a validator." The design does not rely on it to validate:
1. The guard allows only the node types it knows (`SELECT`, joins, CTEs, aggregates, …) and rejects anything else. It is an allowlist over the tree, not a blocklist of bad keywords.
2. It always parses with `dialect="bigquery"`, as the README advises, never with the lenient default superset dialect.
3. The BigQuery dry run is the validator. It is free ("you are not charged for performing a dry run", [running queries](https://docs.cloud.google.com/bigquery/docs/running-queries)) and rejects what BigQuery would reject.
4. In production, row access policies and column-level security sit below the guard ([ADR 0003](0003-layered-data-access-enforcement.md)).

## Considered Options

- **Regex or keyword checks**: cannot rewrite a query, and comments, quoting and CTE names get around them. Rejected.
- **sqlparse**: "intentionally non-validating" ([docs](https://sqlparse.readthedocs.io/en/latest/intro.html)), and it gives a token tree rather than an expression tree, so scope injection would be string surgery. Rejected.
- **GoogleSQL (formerly ZetaSQL)**: the analyzer for the language BigQuery uses, with semantic checks against a catalog, so it is the strongest validator. But it offers no Python API ("Java APIs, implemented by calling a local RPC server"), and it states "We do not provide any guarantees of API stability" and "This is not an officially supported Google product" ([repo](https://github.com/google/zetasql)). It would add a JVM sidecar to every Chat API instance. It remains the option if the guard ever needs full semantic resolution.
- **sqlparser-rs through sqloxide**: a fast Rust parser with Python bindings, visitor-based changes and round-tripping back to SQL ([sqloxide](https://github.com/wseaton/sqloxide)). But it is just as lenient: it "accepts queries that specific databases would reject" ([sqlparser-rs](https://github.com/apache/datafusion-sqlparser-rs)). Its pages show no BigQuery dialect and no example of inserting a subquery, and the binding is a small community project. No advantage for this guard, so rejected.
- **BigQuery dry run alone**: a real, free validator, but it gives no syntax tree, so it can neither check columns nor inject Brand scope. Kept as the second step, not a replacement.
- **Data platform only** (row access policies and column security): the filter "functions like a `WHERE` clause" on the server ([row-level security](https://docs.cloud.google.com/bigquery/docs/row-level-security-intro)). It applies per User only when queries run with the User's token, and the prototype's public dataset cannot carry policies at all. Kept as layer 1 in production, not a replacement.
- **An LLM judge of the SQL**: not deterministic, and it can itself be prompt-injected. The model is never the authorization layer ([ADR 0003](0003-layered-data-access-enforcement.md)). Rejected.

## Consequences

- The guard's behavior is pinned by the `pytest` safety suite, written before the guard ([07](../hld/07-quality-assurance.md)). sqlglot is locked in `uv.lock`, and an upgrade must pass that suite, because a lenient parser can change what it accepts between versions.
- A warehouse change means a dialect setting, a run of the safety suite, and that warehouse's own validator in place of the dry run.
- **The dry-run byte check is blind under row-level security.** "Dry runs on tables that are masked by row-level security always return 0 bytes" ([running queries](https://docs.cloud.google.com/bigquery/docs/running-queries)). When production queries run as the User under row access policies, an over-cap query is not caught at the dry run. `maximum_bytes_billed` still caps it at execution. The baseline, the service account without row access policies, is not affected.
