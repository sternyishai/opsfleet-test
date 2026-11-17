# Golden Trio retrieval is a fixed step before the analysis loop

Req 1 says the agent **must** use the Golden Bucket. If retrieval is only a tool the analysis model may call, that "must" holds only as long as the model follows its prompt. So retrieval is a fixed node of the agent graph:

- **`retrieve_trios`** runs on every `analysis` turn, after the intent router and before the ReAct sub-agent. It searches the Golden Bucket through the `TrioRetriever` port, with the User's Brands taken from graph state, and puts the matches above the similarity threshold into the sub-agent's context as a delimited reference block.
- **The search question comes from the router.** The same Flash-Lite call that returns the action also returns a standalone search question. For a follow-up such as "and NY?", that question carries the earlier context ("why are NY users underspending compared to CA?"). No extra model call is added.
- **`search_golden_trios` stays a tool.** A multi-step question or a report can need several searches with different wording. The sub-agent calls the tool for those, the same way as before.
- **A retrieval gap** is a turn that ran `run_sql` with no Trio above the threshold, a signal that analysts should write a Trio for that kind of question ([02](../hld/02-hybrid-intelligence.md)). Turns that only list or open reports, or ask about the schema, do not count.

This extends the rule of [ADR 0004](0004-hybrid-agent-graph.md): fixed where the system must be safe, **or where a step must happen**; flexible where the model must think. Analysis stays a ReAct loop; only its first step is fixed.

## Considered Options

- **Tool only, prompt-guided** (the earlier design): the most flexible, but a skipped search is invisible. A turn where the model never searched looks the same as a turn where no Trio matched, so retrieval health cannot be trusted. It also spends one analysis-model round trip on a call whose input we already know. Rejected.
- **Forced tool call** (Gemini function-calling mode `ANY` on the first step): guarantees the search, but still pays the model round trip and lets the model word the query. Rejected.
- **Fixed pipeline** (retrieve → write SQL → answer, no loop): the cheapest, but it cannot handle multi-step questions ([ADR 0004](0004-hybrid-agent-graph.md)). Rejected.

## Consequences

- One Flash call fewer per analysis turn in the cost estimate ([11](../hld/11-operations.md)), in exchange for one embedding and one Postgres query.
- Turns that need no Trio still carry the top matches above the threshold, a few hundred to about 1.5k tokens at most.
- If retrieval fails, the turn continues from the schema alone and is flagged as degraded ([06](../hld/06-resilience.md)).
- The offline ablation becomes simpler: the correctness suite runs with the node on and off ([07](../hld/07-quality-assurance.md)).
- The router gains one output field and its per-step eval gains a case set for standalone questions.
- The prototype retrieves over SQLite with cosine similarity in Python, from the seed Trios in `seed/trios/`. With embeddings turned off, the node calls the Null `TrioRetriever`, which returns nothing, so the graph is the same either way.
- For a Manager, all-Brand Trios are filtered out ([ADR 0005](0005-golden-trios-teach-method-not-figures.md), Q18), so the node often runs and finds nothing.
