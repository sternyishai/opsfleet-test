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

## Phase 1: HLD → PR #2 (~2–3 h) — drafted, PR open

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
- [x] every file has been reviewed and iterated
- [x] `decision-log.md` and `open-items.md` are folded into the HLD
- [x] `/mock-interview audit` shows all **design** items as met
- [ ] one `/mock-interview interview` rehearsal is done

## Phase 2: LLD → PR #3 (~1–1.5 h) — done, PR open

**Branch:** `docs/lld`

**Prompt:**

> Read docs/hld/, docs/adr/, docs/lld/README.md and docs/work-plan.md. Build the code-first LLD: a `uv` project skeleton, Pydantic schemas, `Protocol` ports, SQL DDL and an ER diagram in `src/`, with explanations in `docs/lld/`. Use the codebase-design skill. Stubs only, no implementation.

**Done when:**
- [x] `uv run python -c "import agent"` works
- [x] every tool and port has its LLD checklist filled in (`docs/lld/README.md`)
- [x] the ER diagram and graph state are documented and match the code

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
6. **Eval (Req 6, small):** `make eval` scorecard: runner, deterministic scorers, the pre-registered bar, JSON + Phoenix sink, the 20 cases already in `evals/cases/` ([LLD 08](lld/08-eval.md)).
7. **Stretch:** seed Golden Trios, the Persona mechanism (chat or `/persona edit`, quality gate, versions in SQLite; `persona.yaml` is version 1), User Profile notes.
8. **Optional: web UI** ([ADR 0010](adr/0010-ui-is-a-plain-folder-with-generated-types.md)): FastAPI adapter (`agent web`, SSE), `make types`, a small React app in `web/` with chat, the Deletion Plan checkboxes and "My reports". Everything the CLI does goes through `ChatService` and `Views`, so the UI adds no backend logic.

**Done when:**
- [x] every slice is an issue with acceptance criteria and links to the LLD: #5–#14 (slice 7 is split into #11 Trios, #12 Persona, #13 Profile notes)
- [x] build guardrails: `CODING_STANDARDS.md` (what `/code-review` checks), a pre-commit hook running `ruff`, `pyright` and `pytest`

## Phase 4: Build, one slice per session (~4–5 h total)

**Prerequisites (once):**
- [ ] a Gemini API key from Google AI Studio, put in `.env`. Needed only for the manual check at the end of slice 1; the tests run without it.
- [x] `gcloud auth application-default login` (BigQuery access)
- [x] `git config core.hooksPath .githooks` in this clone (commit-message and pre-commit hooks)
- If a step is unclear, use `/mattpocock-skills:wizard`.

**Branch per issue:** `feat/<n>-<slug>`, e.g. `feat/6-query-guard`

**Prompt** (one issue per session; `/clear` between issues):

> /mattpocock-skills:implement #N

`implement` reads the issue, builds it test-first with the `tdd` skill at the seams listed in the issue, runs `/code-review` (Standards against `CODING_STANDARDS.md`, Spec against the issue) and commits. Keep `docs/lld/` in sync with any interface change.

**Loop:** `/mattpocock-skills:implement #N` → PR (closes #N) → merge → retro *if the table below says so* → `/clear`.

**When to run a retro.** Run it in the slice's own session, after the merge and before `/clear`, while the session still holds what went wrong. Only some slices get one. A retro is worth its cost when the slice does one of two things. It sets a pattern that later slices reuse, so a lesson would otherwise be learned again. Or it carries a guarantee where a missed gap is a leak, not a bug. Every other slice still ends with the usual `docs/progress.md` handoff.

| Slice | Issue | Retro | Why |
|---|---|---|---|
| 1 walking skeleton | #5 | ✅ done | First slice: tunes the process itself (context use, SDK gotchas, guardrails) |
| 2 safety | #6 | ✅ done | The core guarantee; the spec review found guard holes the tests missed. Its PII filter and Brand scope are reused by #8 and #11 |
| 3 resilience | #7 | **yes** | Sets the error, retry and fallback patterns every later slice follows; first slice tuned against real model behaviour |
| 4 Saved Reports + Deletion Plan | #8 | **yes** | First SQLite store and first pause and resume (`interrupt()`); #12 and #13 reuse both |
| 5 observability | #9 | no | Adds to the tracer the earlier slices already call |
| 6 eval | #10 | **yes** | First run against the real model: its scorecard sets the limits to tune and which stretch slices are worth doing |
| 7a–7c stretch | #11–#13 | no | Built on patterns already reviewed |
| 8 web UI (optional) | #14 | no | A separate front end over `ChatService`; nothing later builds on it |
| end of Phase 4 | none | **yes** | One last retro over the whole build before Phase 5, while the problems are still fresh |

**How to run one:** `/mattpocock-skills:retro`, then commit its fixes as `docs: record the slice N retro`.

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
