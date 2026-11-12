---
name: design-iterate
description: Take a design question or proposed change, test it against the current design and outside sources, recommend keep or change, and map its blast radius across every design file so the change lands with no conflicts.
argument-hint: "<design question or proposed change>"
disable-model-invocation: true
---

Turn one challenge to the Retail Analysis Assistant design ("why isn't X a must step?", "should Y move to Z?") into a defended decision, and, if it changes the design, into a change plan that leaves **no conflicts** between files. The docs change between sessions, so read them fresh; memory of an earlier read is a hint, never the answer.

Find the files with the **"Where the design lives"** table and the precedence rule ("When files disagree") in [`.claude/skills/design-qa/SKILL.md`](../design-qa/SKILL.md). Use the glossary's terms, never the synonyms it lists under _Avoid_.

## Writing the reply

The reply is a conversation, read in a terminal: make it **scannable**. Short wording cuts words, never facts: every number, quote, source link and `file:line` from the steps stays in.

- **Verdict first**, in one or two lines, before any evidence.
- **One line per item**: each step-2 argument, each reply to it, each source. A sentence that needs a second line becomes two bullets.
- **Tables for anything with columns**: arguments and sources, options and costs, hits and classes. Put the reasoning in the cells, not in paragraphs around the table.
- **Report results, not process**: show what each step found, not how you searched.
- **Group the quiet hits**: all **still true** hits share one table row; **change** and **conflict** hits get a row each.
- **End on the decision**: one plain question the user must answer, or "approve to apply".

## Steps

1. **Frame.** State the question in one line, the Req(s) of `ASSIGNMENT.md` it touches, and its exact wording there (a "must" in the assignment carries weight). Done when you can quote what the client asked and what the design currently says, with both locations.

2. **Current reasoning.** Read every passage that decides the point: the HLD section, its ADR (including _Considered Options_), the decision-log entry, and any assumption or open question in `12`. Write the design's case for the current choice at its strongest. Done when every argument for the current design is listed, each with its source.

3. **Outside evidence.** Check how the point is handled in practice, from primary sources: library docs through `context7` or `langchain-docs` (LangGraph, pgvector, BigQuery), vendor docs, well-known engineering write-ups, via web search. Search in generic technical terms ("LangGraph mandatory retrieval node before ReAct agent"), never with the client's, the user's or the repo's name. Done when each claim you will rely on has a link, or is marked as your own judgement.

4. **Recommend.** Give a verdict: **keep** (and how to defend it), **change**, or **ask the client** (it belongs in `12`). Lay out the options in a table: option, what it costs (latency, cost, complexity, using the design's numbers from `11`), what it buys, which Req it serves. Answer every argument from step 2 head-on. Done when the verdict names the trade-off accepted and every step-2 argument has a reply.

5. **Blast radius** (only for **change**). Sweep for every passage the change touches:
   - grep each affected term, tool name, node name, metric and number over `ASSIGNMENT.md`, `GLOSSARY.md`, `docs/` (HLD, ADRs, decision log, `12`, `progress.md`, `lld/`), `src/` if it exists, and `.claude/skills/` (the mock-interview checklist cites the design);
   - check the diagrams too: Mermaid blocks in the HLD, and the text in `docs/hld/diagrams/*.excalidraw` (the PNG is exported from it);
   - catch the indirect hits: a sentence that becomes false without naming the term ("retrieval stays a tool call"), a metric whose definition shifts, a principle in `00` the change bends, a prototype-vs-production row.

   Classify each hit as **change** (and how), **still true**, or **conflict** (two files already disagree). Done when every grep hit is classified and every changed sentence has a replacement written.

6. **Change plan.** Present, in order: the verdict, the sources, then a table of files → section → exact change, with the ADR first (a changed decision gets a new or amended ADR before the HLD follows it). End with a two- or three-sentence **"Say it in the interview"**. Then stop and wait for approval.

7. **Apply** (on approval). Follow the review loop in `docs/work-plan.md`: one file per step, a handoff note listing anything not backed by a source, a commit per approved file (Conventional Commits, scopes from `CLAUDE.md`). After the last file, re-run the step-5 greps. Done when every hit reads consistently with the new design, and the result of that final sweep is reported.
