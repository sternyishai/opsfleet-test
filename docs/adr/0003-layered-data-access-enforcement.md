# Data access is enforced outside the model, in layers

The model is never the authorization layer. Brand scoping and PII protection are enforced by code and by the data platform, in four layers:

1. **Data platform**: in production the dataset is copied into our own project, and BigQuery row access policies filter rows by the caller's identity (the agent queries on behalf of the User). Column-level security hides PII columns.
2. **Query guard**: every generated SQL statement is parsed into an AST. Only `SELECT` on allowlisted tables and columns is allowed, a `LIMIT` and a bytes-billed cap are forced, and every table reference is rewritten into a subquery filtered to the User's Brands.
3. **Output filter, at a single exit**: one outbound function in the Chat API is the only code that sends anything to a channel, and it scans every message for PII: progress events, confirmation prompts, catalog messages and the final answer. `save_report` runs the same filter before it writes, so stored Saved Reports are filtered too. The filter is a property of the way out, not a node the graph happens to pass through.
4. **Least privilege**: a read-only service account with no DDL or DML.

The prototype runs on `bigquery-public-data`, where we cannot add row access policies, so it implements layers 2–4. Layer 2 then carries the scoping guarantee alone.

PII columns (`first_name`, `last_name`, `email`, `street_address`, `postal_code`, `latitude`, `longitude`) are absent from the queryable schema. Requests for PII are refused, but analysis is never refused for that reason: Customers are shown by `user_id` with non-identifying attributes. A Manager's Customer analysis covers only spend on their own Brands.

## Considered Options

- **Prompt-only filtering**: trivially bypassed by prompt injection, or the model simply forgets. Rejected.
- **Semantic layer / metric tools only**: very safe, but too rigid for open-ended "why" questions. Kept as a possible future addition, not the primary mechanism.
- **Post-query result filtering**: aggregates have already mixed in out-of-scope rows by then. Rejected.
- **Output filter only as a graph node before `respond`**: misses everything that leaves earlier. Progress events stream from inside nodes and tools, a confirmation prompt leaves at `interrupt()`, and `save_report` writes during the analysis loop. Replaced by the single exit above.
- **Output filter in the Channel Gateway**: covers what Users see, but not Saved Reports, and checkpoints would hold unfiltered text. Rejected.
