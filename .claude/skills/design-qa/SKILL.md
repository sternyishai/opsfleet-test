---
name: design-qa
description: Answer questions about this repo's Retail Analysis Assistant design (why, how, what was decided, what if) from its design docs, with linked sources. Use when the user asks about a design decision, component, flow, trade-off or requirement.
argument-hint: "<question about the design>"
---

Answer the user's design question the way the design's author would defend it to the OpsFleet panel: direct, reasoned, and **traced** to the files. The docs are the source of truth, and they change between sessions, so read them fresh for every question; memory of an earlier read is a hint, never the answer.

## Where the design lives

| Question about | Read first | Then |
|---|---|---|
| A term (User, Manager, Trio, Persona, …) | `GLOSSARY.md` | the HLD file that uses it |
| A big choice and its rejected alternatives | `docs/adr/` (one decision per file) | the HLD section it drives |
| The system as a whole, components, data flow, where data lives | `docs/hld/00-overview.md`, `docs/hld/01-architecture.md`, `docs/hld/diagrams/` | |
| A requirement (Req N of `ASSIGNMENT.md`) | `docs/hld/0(N+1)-*.md`: Req 1 → `02`, …, Req 8 → `09` | `docs/decision-log.md`, section for that Req |
| Why these services, models, framework | `docs/hld/10-stack-rationale.md` | ADRs 0001, 0002 |
| Deployment, scale, SLOs, cost, security | `docs/hld/11-operations.md` | decision log § Scale, async processing & SLOs |
| Assumptions, client answers, open questions, POCs | `docs/hld/12-assumptions-and-open-questions.md` (the single list) | |
| Schemas, tool contracts, ports | `docs/lld/`; code in `src/` once it exists | |
| A detail, or why something small was chosen | `docs/decision-log.md` (sections by Req) | |
| What the client asked for | `ASSIGNMENT.md` | |

When files disagree: code beats the LLD on interfaces; the HLD is the current design; an ADR holds the reasoning for its decision; `12` holds assumptions. Any disagreement is a **finding**: report it with both locations, and check `git log -1 -- <file>` to say which is newer.

## Steps

1. **Locate.** Find every passage that bears on the question, using the map above and `grep` over `docs/`, `GLOSSARY.md` and `ASSIGNMENT.md`. Done when you have read each relevant section in full, not just the matching line, plus the ADR behind it if one exists.
2. **Answer.** Write, in this order:
   - **Answer:** one to three sentences that settle the question.
   - **Why:** the reasoning, the alternatives rejected and why, the trade-off accepted. Use the design's numbers where they exist (latency targets, retry limits, scale, costs).
   - **Prototype vs. production**, when the two differ.
   - **Sources:** links as `[file](path#section)`, one per claim group.
   - **Say it in the interview:** for why- and trade-off questions, the answer in two or three spoken sentences.
   Use the glossary's terms, never the synonyms it lists under _Avoid_.
3. **Mark the edges.** For anything the docs do not settle, say **"Not decided in the design"**, give your recommended answer labelled as a proposal, and name where it would be recorded: the decision log, or `12` if it is a client question. Report every finding from step 1 here too.

The answer is done when every claim in it is backed by a linked source or explicitly marked as a proposal.

Answer only. Editing a design file happens when the user asks for it, following the review loop in `docs/work-plan.md`.
