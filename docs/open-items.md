# Open items: assumptions, client questions, POCs

Feeds the HLD's "Assumptions" section.

## Assumptions (to confirm with OpsFleet)

- `thelook` has no User↔Brand link, so Brand Assignments are defined by us (seeded).
- Executives may analyze all Brands; Managers only their Brand Assignments; anyone else is denied by default. (The assignment only says "each user may only analyze data on products related to them".)
- An authenticated User with no role and no Brand Assignment gets no data (default deny).
- The data team publishes the four tables into a dataset we can govern (row access policies, policy tags), since neither can be added to `bigquery-public-data`.
- Users have Google identities through SSO, so queries can run with their own credentials and row access policies apply to them.

## Confirmed by client (Harry, 2026-11-11)

- A Saved Report is question + SQL + answer. Only its author can see, modify or delete it. Reports are never shared, and an Executive's broader analytics access does not extend to other Users' reports.
- "Client X" means any entity named in a report (Brand, category, …). Customers may be referenced by `user_id` or a stable pseudonymous ID, never by name or other PII.
- Deletion is a hard delete. Showing the exact list of matching reports before deleting is the expected confirmation approach.

## Confirmed by client (Harry, 2026-11-12)

- **Scale is low (internal tool).** Tens to a few hundred executives, with only a few active at once. Production sizing: ~100 active Managers per day × ~10 questions = **~1,000 questions/day**, ~100 Saved Reports/day, ~1M new rows/day from daily data loads.
- **Response time:** simple questions ~10–30 s; long reports ~1–2 min. The exact SLO targets are ours to define and justify.
- **Prototype observability:** agent-level metrics plus local tracing/logging for deep dives. Production tooling is described in the HLD.
- **Data is out of scope for the agent.** Another team owns ETL, modeling and connectors; the tables are raw. The agent only builds and runs SQL against the data. It writes only its own state: Saved Reports, audit and observability logs (and, by our design, User Profiles and Trio Candidates).

## Assumptions about the data team

- Fact tables are partitioned by date (and ideally clustered, e.g. by product/brand), so date-bounded queries stay cheap as ~1M rows/day accumulate.
- Data loads daily, so answers say "data as of <last load>".

## POCs to run

- **Input guardrail**: decide whether a third-party service (e.g. Model Armor, Lakera) is needed, or whether a hybrid of rules/regex + a cheap LLM classifier is enough. Measure on an injection/off-topic test set.

## Client questions

- May an Author's Saved Report be *proposed* (anonymized, figures stripped) as a Golden Trio Candidate for analyst review, given that reports are private?
- When an Author deletes a Saved Report, we delete any pending Trio Candidate derived from it, but keep an already-approved Golden Trio. Is that acceptable?
- Retention for the interaction store: we assume 90 days for raw traces and indefinite retention for anonymized aggregates.
