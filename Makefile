eval: ## run the eval suites (docs/lld/08-eval.md); ARGS="--suite safety --trials 3"
	uv run agent eval $(ARGS)

test:
	uv run pytest

check:
	uv run ruff check . && uv run pyright && uv run pytest



# --- web UI (optional, ADR 0010) --------------------------------------------------------------

dev: ## the API (dev identity on, 127.0.0.1:8000) and the Vite dev server (http://localhost:5173)
	pnpm --dir web install --frozen-lockfile
	trap 'kill 0' EXIT; \
	WEB_DEV_IDENTITY=true uv run --extra web agent web & \
	pnpm --dir web dev

types: ## regenerate web/openapi.json and web/src/api/schema.d.ts from the Pydantic models
	uv run --extra web python -m agent.adapters.web.openapi > web/openapi.json
	pnpm --dir web run types

types-check: ## fail when the committed OpenAPI document or TypeScript types are stale
	$(MAKE) types
	git diff --exit-code -- web/openapi.json web/src/api/schema.d.ts

.PHONY: eval test check dev types types-check
