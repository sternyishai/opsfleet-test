# Work plan

How the assignment gets delivered, step by step. Each phase runs in a **new Claude Code session**, on its own branch, and ends with a PR merged into `main`, so every session starts from a clean `main`.

## How context moves between sessions

A chat session is disposable. **The repo is the memory.** Three things carry context from one session to the next:

| Carrier | What it holds | Who updates it |
|---|---|---|
| **Design files** (`GLOSSARY.md`, `docs/adr/`, `docs/hld/`, `docs/lld/`, `src/`) | Everything decided and written | Claude during the session, committed after each approved step |
| **[`docs/progress.md`](progress.md)** | Where we stopped: done / in progress / next, decisions made this session that aren't written anywhere yet, open questions, and **the exact prompt for the next session** | Claude, at the end of every session |
| **Claude's project memory** | A pointer to this plan and `progress.md` | Claude, when you say "update memory" |

So a new session needs only one prompt: *"Read docs/work-plan.md and docs/progress.md, then continue."* The prompt stored in `progress.md` adds which files to read for that step.

## Before every session

```bash
git checkout main && git pull
git checkout -b <branch-for-this-phase>    # or check out the existing branch if the phase spans sessions
```

Then paste the **"Next session prompt"** from `docs/progress.md`.

## During a session: the review loop

1. **One file per step.** Claude drafts one file, then stops.
2. **Handoff note.** After each file, Claude states (a) which ADRs and decision-log entries it is based on, and (b) anything it added that is **not** in those sources. You approve or remove every item under (b).
3. **Review comments in the file.** While reading, write `<!-- REVIEW: … -->` comments where the problem is, then say *"address every REVIEW comment in <file>"*.
4. **Approve → commit.** *"Approved"* means Claude commits that file (`docs(hld): draft 02 hybrid intelligence`), and only then moves on.
5. **Watch the context.** Run `/context` now and then. When it's high, finish the current file, do the end-of-session steps, and start a new session.

## At the end of every session

Say: **"wrap up the session"**. Claude then:

1. Updates `docs/progress.md` (status, unwritten decisions, open questions, next session prompt).
2. Commits (Conventional Commits; the hook enforces it) and pushes.
3. Opens or updates the PR, if the phase is complete.
4. Updates its project memory.

You merge the PR on GitHub ("Rebase and merge") when the phase is done.

---

## Phase 0: Design foundation ✅ (PR #1, merged)

Glossary, ADRs 0001–0006, decision log, open items, LLD scope.

## Phase 1: HLD → PR #2 (~2–3 h)

**Branch:** `docs/hld`

Phase 1 is split into **4 sessions** so each one stays well inside the context window. Each session reads only what its files need. The exact prompt for each session is kept in `docs/progress.md`.

| Session | Files | Reads |
|---|---|---|
| **1A** | `00-overview`, `01-architecture`, hero diagram | glossary, all ADRs, decision log |
| **1B** | `02`–`05` (Reqs 1–4) | glossary, ADRs 0003–0005, matching decision-log sections, `00`/`01` |
| **1C** | `06`–`10` (Reqs 5–8, stack) | glossary, ADRs 0001/0002/0006/0007, matching decision-log sections, open items, `00`–`05` |
| **1D** | `11`–`12` (operations, assumptions) | glossary, decision-log sections, open items, `00`–`10` |

**Planned files:**

| File | Contents |
|---|---|
| `00-overview` | Summary, hero diagram, how to read |
| `01-architecture` | Components, services, data flow, chat-turn sequence diagram |
| `02`–`09` | One file per requirement, each ending with "In the prototype: …" |
| `10-stack-rationale` | Cloud, models, framework, author's experience |
| `11-operations` | Deployment, scale, SLOs, cost, security |
| `12-assumptions` | Assumptions and client questions |

**Done when:**
- [ ] every file has been reviewed and iterated
- [ ] `decision-log.md` and `open-items.md` are folded into the HLD
- [ ] `/mock-interview audit` shows all **design** items as met
- [ ] one `/mock-interview interview` rehearsal is done

## Phase 2: LLD → PR #3 (~1–1.5 h)

**Branch:** `docs/lld`

**Prompt:**

> Read docs/hld/, docs/adr/, docs/lld/README.md and docs/work-plan.md. Build the code-first LLD: a `uv` project skeleton, Pydantic schemas, `Protocol` ports, SQL DDL and an ER diagram in `src/`, with explanations in `docs/lld/`. Use the codebase-design skill. Stubs only, no implementation.

**Done when:**
- [ ] `uv run python -c "import agent"` works
- [ ] every tool and port has its LLD checklist filled in (`docs/lld/README.md`)
- [ ] the ER diagram and graph state are documented and match the code

## Phase 3: GitHub issues (~30 min)

**Branch:** none (issues only)

**Prompt:**

> Read docs/hld/, docs/lld/ and docs/work-plan.md. Create GitHub issues as vertical slices following docs/agents/issue-tracker.md, labeled `ready-for-agent`, each one referencing the ports and schemas it implements and including acceptance criteria. Use the slice order in docs/work-plan.md.

**Slice order:**

1. **Walking skeleton:** CLI → graph → Gemini → `run_sql` on BigQuery, happy path only.
2. **Safety (Req 2):** query guard, Brand scoping, PII filter, `pytest` safety suite.
3. **Resilience (Req 5):** dry run + self-correction, retries and model fallback, rate limiter, error catalog.
4. **Saved Reports + Deletion Plan (Req 3):** create a report with action items; confirm-before-delete with `yes / no / yes except N`.
5. **Observability (Req 7):** JSONL traces, `/trace`, Phoenix export.
6. **Eval (Req 6, small):** `make eval` scorecard.
7. **Stretch:** seed Golden Trios, `persona.yaml`, User Profile notes.

**Done when:**
- [ ] every slice is an issue with acceptance criteria and links to the LLD

## Phase 4: Build, one slice per session (~4–5 h total)

**Prerequisites (once, before slice 1):**
- [ ] a Gemini API key from Google AI Studio, put in `.env`
- [ ] `gcloud auth application-default login` (BigQuery access)
- If either step is unclear, use `/mattpocock-skills:wizard`.

**Branch per issue:** `feat/<n>-<slug>`, e.g. `feat/2-query-guard`

**Prompt:**

> Work on issue #N with the tdd skill. Read docs/lld/ for the interfaces. Keep docs/lld/ in sync with any interface change.

**Loop:** red → green → refactor → `/code-review` → PR (closes #N) → merge.

**Done when:**
- [ ] slices 1–6 are merged (7 if time allows)
- [ ] `uv run pytest` is green on `main`

## Phase 5: Ship anywhere (~1 h)

**Branch:** `docs/readme`

**Prompt:**

> Read docs/work-plan.md. Write the README (setup, example run, links into docs/hld/), `.env.example` and the Dockerfile. Then verify a clean clone works by following only the README.

**Done when:**
- [ ] a clean clone runs by following the README alone (native `uv` and Docker)
- [ ] the README includes screenshots of a CLI session and a Phoenix trace

## Phase 6: Final check & submit (~30 min)

**Prompt:** `/mock-interview both`

**Done when:**
- [ ] the top gaps from the audit are fixed
- [ ] the repo is public
- [ ] the link is submitted

---

## If time runs short

Cut from the end, in this order:

1. Slice 7 (stretch)
2. Phoenix export (JSONL + `/trace` is enough)
3. The size of `make eval`

Never cut the safety suite or the delete-confirmation flow: they are prototype requirements.
