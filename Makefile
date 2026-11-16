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

web: ## build the UI and serve it with the API on http://127.0.0.1:8000 (one process, dev identity on)
	pnpm --dir web install --frozen-lockfile
	pnpm --dir web build
	WEB_DEV_IDENTITY=true WEB_UI_DIR=web/dist uv run --extra web agent web



# --- Docker (README.md § Docker) ----------------------------------------------------------------
# Secrets come from .env and your gcloud application-default credentials at run time; data/ and
# traces/ are bind-mounted, so the container and a native run share Saved Reports and traces.

IMAGE ?= retail-agent
AS ?= ceo
PORT ?= 8000
ADC ?= $(HOME)/.config/gcloud/application_default_credentials.json
DOCKER_RUN = docker run --rm --env-file .env \
	-e GOOGLE_APPLICATION_CREDENTIALS=/gcp/adc.json -v "$(ADC)":/gcp/adc.json:ro \
	--add-host=host.docker.internal:host-gateway -e PHOENIX_ENDPOINT=http://host.docker.internal:6006 \
	-v "$(CURDIR)/data":/app/data -v "$(CURDIR)/traces":/app/traces

docker-build: ## build the image: the web UI (Node stage) and the agent (Python stage)
	docker build -t $(IMAGE) .

docker-chat: ## the CLI in the container; AS=manager-a to pick another User
	mkdir -p data traces
	$(DOCKER_RUN) -it $(IMAGE) chat --as $(AS)

docker-web: ## the web UI in the container on http://127.0.0.1:$(PORT) (host loopback only)
	mkdir -p data traces
	$(DOCKER_RUN) -e WEB_DEV_IDENTITY=true -e WEB_DEV_IDENTITY_ANY_HOST=true \
		-p 127.0.0.1:$(PORT):8000 $(IMAGE)

.PHONY: eval test check dev web types types-check docker-build docker-chat docker-web
