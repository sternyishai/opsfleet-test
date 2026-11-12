## Agent skills

### Issue tracker

GitHub Issues on sternyishai/opsfleet-test via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Commits

Conventional Commits, enforced by `.githooks/commit-msg` (enable per clone: `git config core.hooksPath .githooks`). The subject line is `type(scope): summary`, where `type` is one of `feat fix docs refactor test chore ci build perf style revert`. Scopes follow the design areas: `hld`, `lld`, `adr`, `glossary`, `agent`, `guard`, `reports`, `obs`, `eval`, `cli`. The summary is imperative and lowercase, at most 72 chars; the body says *why*.

### Excalidraw skill

Port 3000 is already taken on this machine, so the skill's default canvas URL (`http://127.0.0.1:3000`) won't reach the right server. Before using the skill, find a free port (e.g. `lsof -iTCP:3001 -sTCP:LISTEN` returns nothing) and point every command at it with `EXPRESS_SERVER_URL=http://127.0.0.1:<port>`, or pass `--url http://127.0.0.1:<port>`. Give the user that URL, not the default one.
