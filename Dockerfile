# One image, both channels (docs/work-plan.md § Phase 5):
#   docker run ... retail-agent                    -> agent web on :8000, the UI next to /api
#   docker run -it ... retail-agent chat --as ceo  -> the CLI
# Secrets (GOOGLE_API_KEY, BigQuery credentials) come in at run time, never at build time;
# data/ (SQLite) and traces/ (JSONL) are volumes. See README.md § Docker.

# --- 1. the web UI: web/ -> web/dist ---------------------------------------------------------
FROM node:24-slim AS web
RUN corepack enable && corepack prepare pnpm@10.34.9 --activate
WORKDIR /web
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

# --- 2. the agent: Python 3.12 + uv, the web extra, no dev dependencies -----------------------
FROM ghcr.io/astral-sh/uv:0.13.6-python3.12-trixie-slim AS app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first, so a source change does not reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra web --no-install-project
COPY src/ src/
COPY seed/ seed/
COPY evals/ evals/
COPY README.md ./
RUN uv sync --frozen --no-dev --extra web
COPY --from=web /web/dist web/dist

RUN useradd --create-home --uid 1000 agent && mkdir -p data traces && chown agent data traces
USER agent
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    WEB_UI_DIR=/app/web/dist
VOLUME ["/app/data", "/app/traces"]
EXPOSE 8000
# google.auth looks for a project of its own and warns without one: give it BIGQUERY_PROJECT.
ENTRYPOINT ["sh", "-c", "export GOOGLE_CLOUD_PROJECT=\"${GOOGLE_CLOUD_PROJECT:-$BIGQUERY_PROJECT}\"; exec agent \"$@\"", "agent"]
CMD ["web", "--host", "0.0.0.0", "--port", "8000"]
