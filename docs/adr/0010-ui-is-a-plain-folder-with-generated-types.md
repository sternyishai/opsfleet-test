# A UI lives in a plain `web/` folder and uses TypeScript types generated from the Pydantic contracts

If the prototype gets a browser UI, it is a small React app (Vite, pnpm) in a top-level `web/` folder. The Python package stays at the repository root, unchanged, so `uv sync && uv run agent chat` still needs no Node. The UI is one more channel: a FastAPI adapter in `src/agent/adapters/web/` receives a message, calls the same `ChatService.handle` as the CLI, and streams the turn's `OutboundMessage`s back as Server-Sent Events. The graph does not change, and every message still leaves through the single outbound exit, so the output PII filter covers the browser like any other channel ([ADR 0003](0003-layered-data-access-enforcement.md)).

The one real cross-language risk is the contracts drifting apart. The Pydantic models are the source of truth: FastAPI's OpenAPI document is turned into `web/src/api/schema.d.ts` with `openapi-typescript` by `make types`, and a check fails when the committed file is out of date. Nobody writes these types by hand.

## Considered Options

- **Nx monorepo**: task caching, affected-only builds and project graphs pay off with many JS/TS projects sharing libraries. Here there would be one Python package and one small app. Python support comes from a community plugin, and every Python-only contributor and reviewer would need Node and Nx to run anything. Rejected for now.
- **pnpm workspaces**: the lightweight step if a second frontend appears (for example an Admin UI sharing components with the chat). Deferred; `web/` becomes a workspace package without being rewritten.
- **Hand-written TypeScript types**: the fastest start, but they drift from the Pydantic models silently. Rejected.
- **WebSocket instead of SSE**: two-way, but a turn is one request and one stream of replies, and SSE works through proxies and IAP with plain HTTP. Rejected.

## Consequences

- `ChannelAdapter` is outbound-only and per turn; inbound goes through `ChatService.handle(message, channel)`, the entry point both the CLI and the web adapter call ([LLD 07](../lld/07-web.md)).
- FastAPI and Uvicorn are an optional extra (`agent[web]`), so the CLI install stays small.
- The web prototype picks a seeded User from a dev-only picker; production identity comes from IAP or SSO in the Channel Gateway, never from the request body.
- Revisit Nx or pnpm workspaces when a second frontend or a slow CI appears.
