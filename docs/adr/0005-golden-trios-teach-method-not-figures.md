# Golden Trios teach method, not figures

Analyst Reports in the Golden Bucket contain real figures that may belong to Brands the requesting User cannot access. Injecting them into the prompt would leak data even though the User's SQL access is correctly scoped. So at ingestion we keep each Trio's Question, its SQL, and the *method and structure* of its report (which metrics, comparisons and reasoning the analyst used), and strip the concrete figures. Each Trio is also tagged with the Brands it covers, and retrieval is filtered by the User's access as defense in depth. Trios teach the agent *how* analysts approached a question; the numbers always come from a fresh, scoped query.

## Considered Options

- **Inject full Trios**: the richest context, but it leaks across Brands. Rejected.
- **Brand-tag filtering only**: Trios often span many Brands, and a single tagging miss leaks data. Kept as the second layer, not the main guarantee.
