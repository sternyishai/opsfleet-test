# 12 · Assumptions and open questions

Every assumption the design rests on, every point the client has already confirmed, and every question still open, in one place. Each assumption says where it is used and what changes if it turns out to be wrong, so a "no" from the client is a known adjustment, not a redesign.

## Confirmed by the client

| Date | Confirmed | Used in |
|---|---|---|
| 2026-11-11 | A Saved Report is question + SQL + answer. Only its Author can see, change or delete it; reports are never shared, and an Executive's wider data access does not extend to other Users' reports. | [04](04-destructive-ops.md) |
| 2026-11-11 | "Client X" means any entity named in a report (Brand, category, …). Customers may be referenced by `user_id` or a stable pseudonymous ID, never by name or other PII. | [03](03-safety-and-pii.md), [04](04-destructive-ops.md) |
| 2026-11-11 | Deletion is a hard delete. Showing the exact list of matching reports before deleting is the expected confirmation. | [04](04-destructive-ops.md) |
| 2026-11-12 | Scale is low: tens to a few hundred executives, few active at once; ~1,000 questions a day, ~100 Saved Reports a day, ~1M new rows a day from daily loads. | [11](11-operations.md) |
| 2026-11-12 | Response time: simple questions ~10–30 s, long reports ~1–2 min. The SLO targets are ours to define. | [11](11-operations.md#slos) |
| 2026-11-12 | Prototype observability: agent-level metrics plus local tracing for deep dives; production tooling is described in the HLD. | [08](08-observability.md) |
| 2026-11-12 | Persona changes: a non-technical User such as the CEO updates the agent's instructions without editing code and without a redeploy. The mechanism is ours to choose. Every change passes an automated quality gate before it goes live, with no manual approval step. | [09](09-persona-management.md) |
| 2026-11-12 | Data is out of scope for the agent: another team owns ETL, modeling and connectors. The agent only reads, and writes only its own state. | [01](01-architecture.md#where-data-lives-and-how-it-is-handled) |
| 2026-11-16 | Setup tooling is our choice: a `requirements.txt` is not required; a uv project or Docker is fine. What matters is a README with clear, accurate setup instructions and an example run, so the team can run it on another machine with their own credentials. | [README](../../README.md) |

## Assumptions

### Access and identity

| # | Assumption | Used in | If it is wrong |
|---|---|---|---|
| A1 | `thelook` has no link between people and Brands, so Brand Assignments are our own table: seeded in the prototype, administered in production. | [03](03-safety-and-pii.md#identity-and-roles) | If an HR or CRM system already holds the mapping, a sync job imports it into the same table. Nothing downstream changes. |
| A2 | Executives may analyze all Brands, Managers only their Brand Assignments, and anyone else is denied by default (an authenticated User with no role gets no data). | [03](03-safety-and-pii.md#identity-and-roles) | New roles (for example a regional manager) are new scope rules in the query guard and new row access policies. The default-deny stays. |
| A3 | Queries run as the agent's read-only service account, and the query guard carries Brand scoping, because SSO identifies Users but gives no BigQuery credentials. Per-User queries, so row access policies apply to the real person, need Google identities and a one-time OAuth consent with a read-only BigQuery scope. | [03](03-safety-and-pii.md#data-platform-production), [10](10-stack-rationale.md) | If the client wants per-User queries, we add the consent flow and encrypted token storage; Users who have not consented stay on the service account. Either way it is still safe; per-User queries add one layer. |

### Data platform and the data team

| # | Assumption | Used in | If it is wrong |
|---|---|---|---|
| A4 | The data team publishes the four tables into a dataset we can govern (row access policies, policy tags), since neither can be added to `bigquery-public-data`. | [03](03-safety-and-pii.md#data-platform-production) | The query guard and the output PII filter still hold, as in the prototype, but the platform layer of [ADR 0003](../adr/0003-layered-data-access-enforcement.md) is missing. We would ask for an authorized view as the minimum. |
| A5 | Fact tables are partitioned by date (ideally clustered by product or Brand), so date-bounded queries stay cheap as ~1M rows a day accumulate. | [11](11-operations.md#scale), [11](11-operations.md#cost-estimate-back-of-the-envelope) | Bytes per query grow with the table. The bytes cap still bounds every query, but more questions hit "query too large", and the cost estimate's BigQuery line grows. |
| A6 | Data loads once a day, so answers say "data as of <last load>" and the result cache is valid until the next load. | [06](06-resilience.md), [11](11-operations.md#cost-controls) | With more frequent loads the cache expires on each load event instead; the "data as of" label stays. |
| A7 | Gemini on Vertex AI is available in the governed dataset's region, so every service runs in one region. | [11](11-operations.md#deployment) | Model calls cross regions. That adds latency and a data-residency question, not a design change. |

### Retention and privacy

| # | Assumption | Used in | If it is wrong |
|---|---|---|---|
| A8 | Raw traces, interaction records and Conversation checkpoints are kept 90 days; anonymized aggregates indefinitely. | [01](01-architecture.md#where-data-lives-and-how-it-is-handled), [05](05-learning-loop.md), [08](08-observability.md) | A setting per store. Shorter raw retention gives the weekly review and replay less history to work with. |
| A8a | Dead-lettered background events are kept 7 days, after about 5 failed deliveries (both Pub/Sub defaults). They hold IDs only. | [01](01-architecture.md#where-data-lives-and-how-it-is-handled), [11](11-operations.md#running-it) | A setting per subscription (retention up to 31 days, attempts 5–100). A shorter window means a fix must ship faster before messages are dropped; the nightly sweeps redo most missed work anyway. |
| A9 | Deletion audit rows are kept indefinitely (metadata only: who, when, count, IDs, titles). | [04](04-destructive-ops.md), [01](01-architecture.md#where-data-lives-and-how-it-is-handled) | A setting. The audit row never holds content, so it does not undo the hard delete. |

### People and operations

| # | Assumption | Used in | If it is wrong |
|---|---|---|---|
| A10 | An AI-ops team owns the agent in production: on-call in business hours, paged at any hour only for a PII hit or an out-of-scope answer. | [08](08-observability.md#knowing-when-slos-alerts-and-probes), [11](11-operations.md#running-it) | The alert routing moves to whoever owns it. The two "any hour" alerts must still have someone behind them. |
| A11 | "Business hours" is one time zone, about 10 hours a day, Monday to Friday. Per-User daily quotas reset at midnight in that zone. | [11](11-operations.md#slos), [06](06-resilience.md) | Several time zones widen the availability window and the on-call hours; quotas reset at each User's local midnight. |
| A12 | Analysts have time to review Trio Candidates and write new Trios, and analysts and business owners join a monthly domain-expert session. | [02](02-hybrid-intelligence.md), [05](05-learning-loop.md) | The Golden Bucket grows only from expert files, and retrieval gaps stay open longer. The agent still works from the schema. |
| A13 | Enough Trios exist to keep a held-out share out of retrieval as correctness references. | [07](07-quality-assurance.md) | The correctness suite relies on seed cases and real failures until the Golden Bucket grows. |
| A14 | Someone labels a set of answers by hand to calibrate the LLM judge before it gates releases. | [07](07-quality-assurance.md) | The judge reports scores but does not gate; execution match and the safety suite gate alone. |
| A15 | Only the CEO holds the `persona_editor` permission, and changes the tone in chat or by direct edit. | [09](09-persona-management.md#who-may-change-it) | If the CEO delegates, an admin grants the permission to another User; no code change. |

### Models and cost

| # | Assumption | Used in | If it is wrong |
|---|---|---|---|
| A16 | Vertex AI is the production model endpoint, under terms that exclude training on customer data. | [10](10-stack-rationale.md), [11](11-operations.md#security) | Another provider is a `.env` change through the model factory, after it passes the per-step evals. |
| A17 | Contracts or keys exist for a second Vertex AI region and a second provider, so the fallback chain can reach them. | [06](06-resilience.md) | The chain ends at Flash-Lite in the primary region. A full Gemini outage then gives the "model busy" message. |
| A18 | The cost inputs in [11](11-operations.md#cost-estimate-back-of-the-envelope): list prices from November 2026, ~5 analysis calls of ~8k tokens per question, ~5 GB billed per question. | [11](11-operations.md#cost-estimate-back-of-the-envelope) | They are replaced by measured values from the prototype and pilot traces. The cost ceilings bound spend either way. |

### Extensions

| # | Assumption | Used in | If it is wrong |
|---|---|---|---|
| A19 | A delivered Saved Report (email, Slack, push) goes only to its Author's own address or channel identity from SSO, which keeps the confirmed rule that reports are never shared. | [01](01-architecture.md#agent-graph), [ADR 0009](../adr/0009-outbound-actions-are-a-deliver-branch.md) | Sending to others needs a recipient rule enforced in code (for example, only Users who may see every Brand in the report), and it changes the confirmed sharing rule for Saved Reports. |

## Questions for the client

Each question has our proposed default, so the design proceeds while it is open.

| # | Question | Our default until answered | Relates to |
|---|---|---|---|
| Q1 | May an Author's private Saved Report be *proposed* (anonymized, figures stripped) as a Trio Candidate for analyst review? | No, until answered: an analyst's approval is not the Author's consent to expose the report. Candidate generation from Saved Reports is off, and Trios come from expert files only. A yes turns it on as a setting; Candidates still need analyst approval | [02](02-hybrid-intelligence.md), [05](05-learning-loop.md) |
| Q2 | When an Author deletes a Saved Report, we delete any pending Trio Candidate derived from it but keep an already-approved Trio. Is that acceptable? | Yes, as described | [04](04-destructive-ops.md) |
| Q3 | Is 90-day retention for raw traces, interaction records and checkpoints acceptable, and indefinite retention for anonymized aggregates and the deletion audit? | As stated (A8, A9) | [01](01-architecture.md), [08](08-observability.md) |
| Q4 | Do all Users have Google identities (Workspace or Cloud Identity), and is a one-time OAuth consent for BigQuery acceptable so queries can run as each User? | No consent flow: queries run as the service account with the query guard (A3) | [03](03-safety-and-pii.md), [10](10-stack-rationale.md) |
| Q5 | Where do Brand Assignments come from today (an HR or CRM system, a spreadsheet), and who changes them? | Administered in our own table (A1) | [03](03-safety-and-pii.md) |
| Q6 | Can the data team publish the four tables into a governed dataset, date-partitioned, and in which region? | Yes (A4, A5, A7) | [03](03-safety-and-pii.md), [11](11-operations.md) |
| Q7 | Who owns and is on call for the agent in production? | An AI-ops team, business hours (A10) | [08](08-observability.md), [11](11-operations.md) |
| Q8 | What are the business hours and time zones of the Users? | One zone, ~10 h, Monday to Friday (A11) | [11](11-operations.md#slos) |
| Q9 | Should anyone besides the CEO be able to change the Persona, for example a chief of staff? | Only the CEO (A15); delegation is an admin grant | [09](09-persona-management.md#who-may-change-it) |
| Q10 | Can we run an executive pilot with 5–8 executives and Managers before launch? | Yes; it sets the trusted-answer baseline | [07](07-quality-assurance.md#evaluating-ux) |
| Q11 | Which analysts can review Trio Candidates and label answers for judge calibration, and how much time a week? | A few hours a week from one or two analysts (A12, A14) | [02](02-hybrid-intelligence.md), [07](07-quality-assurance.md) |
| Q12 | How many Trios exist today, and in what format? | Enough for retrieval plus a held-out share (A13); ingestion parses one file per Trio | [02](02-hybrid-intelligence.md), [07](07-quality-assurance.md) |
| Q13 | Which channel comes first: Slack, Teams or web? | Whichever leadership already uses; each is one Gateway adapter | [01](01-architecture.md) |
| Q14 | Is a second model provider acceptable for fallback, and which one? | A second Vertex AI region first, then one provider the client approves (A17) | [06](06-resilience.md) |
| Q15 | May a User send a report to someone other than themselves, by email or Slack? | No: only to the Author (A19) | [04](04-destructive-ops.md), [ADR 0009](../adr/0009-outbound-actions-are-a-deliver-branch.md) |
| Q16 | Which outbound capability, if any, should come first: email, Slack posting or push notifications? | None until asked; email first, as the assignment's example. Each is one `Delivery` adapter | [01](01-architecture.md#agent-graph), [ADR 0009](../adr/0009-outbound-actions-are-a-deliver-branch.md) |
| Q17 | Deleting a Saved Report removes it from the library. Must it also purge copies of its answer in Conversation history, interaction records and traces, or is the 90-day age-out enough? | Library removal plus the 90-day age-out (A8) | [04](04-destructive-ops.md#executing-the-deletion) |

## POCs to run

| POC | Question it answers | How |
|---|---|---|
| **Input guard** | Is a managed service (Model Armor, Lakera) needed, or is the hybrid of deterministic rules plus a Flash-Lite classifier enough? | Run both on an injection and off-topic test set; compare catch rate, false refusals, latency and cost. The test set joins the red-team suite either way ([03](03-safety-and-pii.md), [07](07-quality-assurance.md)). |

## To measure, not to ask

These are starting values in the design, set from traces rather than from the client:

- the cost inputs: tokens, analysis steps and bytes per question ([11](11-operations.md#cost-estimate-back-of-the-envelope));
- timeouts, retry counts and circuit-breaker thresholds ([06](06-resilience.md));
- the Trio similarity threshold and the share of turns with a retrieval gap ([02](02-hybrid-intelligence.md));
- per-User quotas, the spend cap and the bytes cap ([11](11-operations.md#cost-controls));
- the trusted-answer baseline, first set by the pilot ([07](07-quality-assurance.md)).
