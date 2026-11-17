# Data access is enforced outside the model, in layers

The model is never the authorization layer. Brand scoping and PII protection are enforced by code and by the data platform, in four layers:

1. **Data platform**: in production the dataset is copied into our own project, where BigQuery row access policies and column-level security can be set. Row policies filter by whoever runs the query. Our queries run as the read-only service account, so the policies cannot tell Users apart unless each query carries the User's own OAuth token, which SSO login does not give us. Until it does, layer 2 carries Brand scoping ([03](../hld/03-safety-and-pii.md#data-platform-production)). Column-level security hides PII columns either way.
2. **Query guard**: every generated SQL statement is parsed into an AST. Only `SELECT` on allowlisted tables and columns is allowed, a `LIMIT` and a bytes-billed cap are forced, and every table reference is rewritten into a subquery filtered to the User's Brands.
3. **Output filter, at a single exit**: one outbound function in the Chat API is the only code that sends anything to a channel or, through a `Delivery` adapter, to an outside destination such as email ([ADR 0009](0009-outbound-actions-are-a-deliver-branch.md)). It scans every message for PII: progress events, confirmation prompts, catalog messages, the final answer and delivered content. `save_report` runs the same filter before it writes, so stored Saved Reports are filtered too. The filter is a property of the way out, not a node the graph happens to pass through. PII columns are already unreachable, so it catches what the guard cannot see: PII the User types or pastes, PII the model invents or echoes from earlier turns, and free text in data we do not control, such as product names.
4. **Least privilege**: a read-only service account with no DDL or DML.

The prototype runs on `bigquery-public-data`, where we cannot add row access policies, so it implements layers 2–4. Layers 3 and 4 protect PII and writes, not Brands, so in the prototype layer 2 carries Brand scoping alone, and it is trusted because it rewrites the syntax tree ([ADR 0011](0011-query-guard-parses-with-sqlglot.md)) and is pinned by the guard's test suite.

PII columns (`first_name`, `last_name`, `email`, `street_address`, `postal_code`, `latitude`, `longitude`) are absent from the queryable schema. Requests for PII are refused, but analysis is never refused for that reason: Customers are shown by `user_id` with non-identifying attributes. A Manager's Customer analysis covers only spend on their own Brands.

## Considered Options

- **Prompt-only filtering**: trivially bypassed by prompt injection, or the model simply forgets. Rejected.
- **Semantic layer / metric tools only**: very safe, but too rigid for open-ended "why" questions. Kept as a possible future addition, not the primary mechanism.
- **BigQuery authorized views or table functions** (for example `scoped_order_items(brands)` shared as an authorized routine): moves Brand scoping into the platform. It needs a dataset we own, so it is production-only, like layer 1. One view per Brand does not scale to thousands of Brands and changing assignments, and a parameterized function still takes `@brands` from the caller, so the trust question stays. A production alternative to layer 2, not chosen as the primary mechanism.
- **Post-query result filtering**: aggregates have already mixed in out-of-scope rows by then. Rejected.
- **Output filter only as a graph node before `respond`**: misses everything that leaves earlier. Progress events stream from inside nodes and tools, a confirmation prompt leaves at `interrupt()`, and `save_report` writes during the analysis loop. Replaced by the single exit above.
- **Output filter in the Channel Gateway**: covers what Users see, but not Saved Reports, and checkpoints would hold unfiltered text. Rejected.
