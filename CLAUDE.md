## Agent skills

### Issue tracker

GitHub Issues on sternyishai/opsfleet-test via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Commits

Conventional Commits, enforced by `.githooks/commit-msg` (enable per clone: `git config core.hooksPath .githooks`). The subject line is `type(scope): summary`, where `type` is one of `feat fix docs refactor test chore ci build perf style revert`. Scopes follow the design areas: `hld`, `lld`, `adr`, `glossary`, `agent`, `guard`, `reports`, `obs`, `eval`, `cli`. The summary is imperative and lowercase, at most 72 chars; the body says *why*.
