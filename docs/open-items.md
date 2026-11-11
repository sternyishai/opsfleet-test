# Open items: assumptions, client questions, POCs

Feeds the HLD's "Assumptions" section.

## Assumptions (to confirm with OpsFleet)

- `thelook` has no User↔Brand link, so Brand Assignments are defined by us (seeded).
- Executives may analyze all Brands; Managers only their Brand Assignments; anyone else is denied by default. (The assignment only says "each user may only analyze data on products related to them".)

## Confirmed by client (Harry, 2026-11-11)

- A Saved Report is question + SQL + answer. Only its author can see, modify or delete it. Reports are never shared, and an Executive's broader analytics access does not extend to other Users' reports.
- "Client X" means any entity named in a report (Brand, category, …). Customers may be referenced by `user_id` or a stable pseudonymous ID, never by name or other PII.
- Deletion is a hard delete. Showing the exact list of matching reports before deleting is the expected confirmation approach.

## POCs to run

- **Input guardrail**: decide whether a third-party service (e.g. Model Armor, Lakera) is needed, or whether a hybrid of rules/regex + a cheap LLM classifier is enough. Measure on an injection/off-topic test set.

## Client questions

- May an Author's Saved Report be *proposed* (anonymized, figures stripped) as a Golden Trio Candidate for analyst review, given that reports are private?
- Retention for the interaction store: we assume 90 days for raw traces and indefinite retention for anonymized aggregates.
