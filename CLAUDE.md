## Agent skills

### Issue tracker

GitHub Issues on sternyishai/opsfleet-test via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Building a slice

One issue per session: branch `feat/<issue>-<slug>` (e.g. `feat/7-resilience`) from an up-to-date `main`, and stop after its PR merges; the next issue starts in a fresh session. Start from the issue's **Implements** table: it names the files. Then read the matching `docs/lld/` section (index: `docs/lld/README.md`) and only the HLD sections it links, not whole packages or whole docs. The live demo the user runs for the PR: `docs/demo.md`.

### Commits

Conventional Commits, enforced by `.githooks/commit-msg`; `.githooks/pre-commit` runs `ruff`, `pyright` and `pytest` (enable both per clone: `git config core.hooksPath .githooks`), and CI (`.github/workflows/check.yml`) runs the same three on every PR. Code is reviewed against `CODING_STANDARDS.md`. The subject line is `type(scope): summary`, where `type` is one of `feat fix docs refactor test chore ci build perf style revert`. Scopes follow the design areas: `hld`, `lld`, `adr`, `glossary`, `agent`, `guard`, `reports`, `obs`, `eval`, `cli`. The summary is imperative and lowercase, at most 72 chars; the body says *why*.

### Excalidraw skill

Port 3000 is already taken on this machine, so the skill's default canvas URL (`http://127.0.0.1:3000`) won't reach the right server. Before using the skill, find a free port (e.g. `lsof -iTCP:3001 -sTCP:LISTEN` returns nothing) and point every command at it with `EXPRESS_SERVER_URL=http://127.0.0.1:<port>`, or pass `--url http://127.0.0.1:<port>`. Give the user that URL, not the default one.
