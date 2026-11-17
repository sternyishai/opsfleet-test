# Golden Trios teach method, not figures

Analyst Reports in the Golden Bucket contain real figures that may belong to Brands the requesting User cannot access. Injecting them into the prompt would leak data even though the User's SQL access is correctly scoped. So at ingestion we keep each Trio's Question, its SQL, and the *method and structure* of its report (which metrics, comparisons and reasoning the analyst used), and strip the concrete figures. Each Trio is also tagged with the Brands it covers, and retrieval is filtered by the User's access as defense in depth. Trios teach the agent *how* analysts approached a question; the numbers always come from a fresh, scoped query. The report itself is never stored: the Trio keeps a figure-free `method_summary`, and the original stays at its `source_uri`, outside the agent's reach. A Trio's SQL needs no stripping for rows, because when the agent reuses it, it runs through the query guard like any other SQL and is rewritten to the User's Brands.

*Amended 2026-11-14:* "figures" means **measured results**, not method parameters. A definition value such as a 90-day churn window is part of the method and stays, declared as a named parameter. Measured results and identifiers (Brand names, Customer IDs) are removed from all three parts of the Trio, not only the report, and the figure check allows a number only where it matches a declared parameter ([02](../hld/02-hybrid-intelligence.md)). Brand-tag filtering stays mandatory: even a method can be sensitive business knowledge. The figure check reads digits; results written in words ("revenue roughly halved", "Jeans was the top category") are caught by the human approval each Trio needs (`approved_by`).

## Considered Options

- **Inject full Trios**: the richest context, but it leaks across Brands. Rejected.
- **Brand-tag filtering only**: Trios often span many Brands, and a single tagging miss leaks data. Kept as the second layer, not the main guarantee.

## Open question: Managers and all-Brand Trios

A Trio is reachable only when its Brands sit inside the User's scope, and an analyst query over every Brand is tagged `all_brands`, which reaches Executives only. Most analyst work spans many Brands, so a Manager will rarely get a Trio, and `retrieve_trios` ([ADR 0008](0008-trio-retrieval-is-a-fixed-step.md)) finds nothing for them. One option: the approving analyst marks a figure-free Trio "method shareable", which opens it to every Manager. Whether a method alone is sensitive is client question Q18 ([12](../hld/12-assumptions-and-open-questions.md)).
