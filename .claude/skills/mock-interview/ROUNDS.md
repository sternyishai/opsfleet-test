# Interview rounds

Each round gives its goal, its seed questions, and what a `strong` answer holds. Seeds are openers: the probes come from the candidate's answer, the prep weak spots, and the market-calibrated probes. A focus area narrows every round to that topic.

## Open (3 min)

"Walk us through the architecture in two minutes."
**Strong:** the main flow end to end, the named services, and where data lives, inside the time box.

## Requirements (one block per requirement 1–8, ~3 min each)

Ask an open, client-style question ("How do you stop a Manager seeing another brand's numbers?"), then probe.
**Strong:** matches the documented decision, names the rejected alternative and why, and says how the prototype proves it (for 2, 3, 5, 7).

## Scale (8 min)

Seeds:
- "What load is this designed for? Users, brands, questions per hour, answer-time target?"
- "What breaks first at 10×? At 100×?" Steer toward: Gemini rate limits and quotas, BigQuery cost per query and bytes scanned, the size of the Golden Bucket and retrieval latency, memory and preference storage per user, conversation state, report library growth.
- "How do you keep one heavy user from starving the others?"
**Strong:** stated assumptions with numbers, the first bottleneck named, and a concrete change per step (caching, quotas, queueing, partitioning, model tiering).

## Trade-offs (8 min)

Seeds:
- "Single agent or multi-agent? Defend it against the other."
- "Why this framework, this model, this data store?" Use the ADRs' rejected options.
- "What's the weakest part of your design? The single point of failure?"
- "Where would you split this into separate services, and when?"
**Strong:** names the cost of its own choice, says when it would switch, and links to the decision record.

## Curveballs (6 min)

Change a requirement mid-interview and ask what changes in the design:
- "Now it's 50 client companies, each with its own dataset."
- "Harry wants answers in Slack, not the CLI."
- "Legal says no data may leave the EU" / "The client drops Gemini."
- "Add emailing reports and web search for trends next sprint."
**Strong:** the change lands in one or two named components; the rest of the design holds. That proves "easy to extend".

## Estimates (5 min)

Make the candidate do the back-of-envelope sum out loud: tokens per question (prompt, retrieved trios, schema, retries), cost per question and per day, BigQuery bytes per query, the worst case with self-correction retries.
**Strong:** stated inputs, arithmetic in the open, a number within an order of magnitude, and the lever that cuts it most.

## Prototype (8 min)

Seeds: "Show me where PII is masked in the code." "Run the delete-confirmation flow." "Show me the trace of a failed query that self-corrected." "What do the tests prove?"
**Strong:** goes straight to the file and test, runs it, and admits what is stubbed.

## Experience (4 min)

- "What's your real experience with this framework and with Gemini?" (deliverable 6 asks for it.)
- "What did you ask us before building, and what did you assume?" Grade against `docs/open-items.md`.
**Strong:** honest level, concrete past use, and clarifying questions that changed the design.

## Close (5 min)

"What would you do differently with two more weeks?" Then: "Any questions for us?" Grade the candidate's questions too.
**Strong:** prioritised, risk-driven next steps; questions about users, success metrics and the production environment.
