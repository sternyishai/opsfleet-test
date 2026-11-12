# Outbound actions are a router-opened `deliver` branch behind a Delivery port

The assignment asks for a system that is easy to extend with capabilities such as "sending reports by email". A capability that sends data out of the system (email, a Slack post, a push notification) is not added as a tool of the analysis sub-agent. It goes through one fixed branch, `deliver`:

- **Only the router opens it**, from the User's own message ("email it to me"). The analysis model can never call it, so text inside query results or Trios cannot make the system send anything.
- **It sends a result that already exists.** `deliver` never runs SQL. It loads a Saved Report by ID, with the Author injected from graph state, the same way the report tools do. An answer that was not saved yet is saved first: asking to send it is an explicit request to keep it ([04](../hld/04-destructive-ops.md)). Raw query rows never leave.
- **The recipient comes from code, never from the model.** By default it is the Author's own address or channel identity from SSO ([12](../hld/12-assumptions-and-open-questions.md)).
- **It confirms first.** The branch pauses with `interrupt()` and shows what will be sent and to whom, the same pattern as the Deletion Plan.
- **It leaves through the single exit.** Delivery adapters are called only from the outbound function that runs the output PII filter ([ADR 0003](0003-layered-data-access-enforcement.md)), so delivered content is filtered like every other message.
- **New destinations are adapters.** A `Delivery` port has one adapter per destination: email, Slack, push. Adding one is an adapter and a wiring change, with no change to the graph ([ADR 0006](0006-ports-and-adapters.md)).

**When the message also asks for analysis** ("create the Q1 report and email it to me"), the router returns `analysis` as the action and `deliver` as a *follow-up action*. Analysis runs first and saves the report, and a fixed edge then runs `deliver` on it. This is the reverse of the deferred request after a delete or Persona change ([01](../hld/01-architecture.md#agent-graph)), because delivery needs the result.

This applies the rule of [ADR 0004](0004-hybrid-agent-graph.md) to a third kind of action: one that **sends data outside the system**. Capabilities that only read and that the model chooses mid-analysis, such as charts or web search, stay tools of the analysis sub-agent.

The branch is added together with the first outbound adapter. Until then, the router keeps its four actions.

## Considered Options

- **A `send_email` tool in the analysis sub-agent** (the earlier wording in 01 and ADR 0004): the cheapest, but the model holds a send action, and an injected instruction in a query result or a Trio could make it send a report to an address it chose. Rejected.
- **A delivery sub-agent** (tools grouped behind their own ReAct loop): isolates context, but adds a model loop to every send and still lets a model decide to send. Grouping tools pays off when one agent has too many tools ([LangChain: multi-agent](https://docs.langchain.com/oss/python/langchain/multi-agent/index#why-multi-agent)); the analysis sub-agent has six. Rejected for delivery; kept as the growth path for read tools.
- **Delivery inside the Channel Gateway**: the Gateway already talks to Slack, but it sits after the single exit and does not know Saved Reports or their Authors. Rejected.

## Consequences

- The router gains one action and an optional follow-up action field, and its per-step eval gains a case set for mixed messages ("… and email it").
- No extra model call on a send: `deliver` is deterministic code plus one confirmation.
- Scheduled reports and push notifications reuse the branch. A Cloud Scheduler job starts a turn as the report's Author and enters `deliver` directly.
- Who may receive a report beyond its Author is a client question ([12](../hld/12-assumptions-and-open-questions.md)). Sending to others would change the rule that Saved Reports are never shared.
- When the analysis sub-agent's read tools grow past roughly 10–20, the first step is filtering tools per role or per turn ([LangChain: selecting tools](https://docs.langchain.com/oss/python/langchain/context-engineering#selecting-tools)). Only if that is not enough are they grouped into specialist sub-agents. Web search is the first candidate, because its results are large.
- Not in the prototype.
