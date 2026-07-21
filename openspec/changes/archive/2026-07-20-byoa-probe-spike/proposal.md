## Why

We need to migrate `bridge-server/` from its current custom WebSocket audio protocol to the Even Realities "Add Agent" / BYOA (Bring Your Own Agent) protocol so the G2's built-in Add Agent mode can talk to it directly — eliminating the custom `glasses-app/` plugin entirely. Public research (the `dAAAb/openclaw-even-g2-bridge-skill` worker, the blog.juchunko.com packet capture, and the CI-Even-Realities project) gives us high confidence on the request/response shape, but five behavioral unknowns remain that block us from writing the production migration with confidence: multi-turn history ownership, `body.user` field presence, HUD error rendering, SSE-vs-JSON tolerance, and response character limits. This spike builds a temporary probe server to resolve those unknowns against real G2 hardware before committing to the production migration design.

## What Changes

- **Add** `probe/` directory containing a standalone FastAPI HTTPS server that:
  - Accepts `POST /` with Bearer auth (any token accepted, logged verbatim)
  - Captures every request (headers, body, client IP, timestamp) to a structured `probe.log`
  - Forwards to the existing LiteLLM upstream (`LITELLM_BASE_URL` + `LITELLM_API_KEY`) with `model: "openclaw"` rewritten to `CHAT_MODEL` and `SYSTEM_PROMPT` injected
  - Returns a plain-JSON OpenAI chat-completion object so the glasses render the LLM reply end-to-end
  - Supports optional diagnostic endpoints (deferred SSE / status-code probes — see design)
- **Add** `probe/README.md` documenting how to run the probe and what observations to capture
- **Add** `probe/pyproject.toml` declaring dependencies under `[project.dependencies]` and managed with `uv` (per repo-root `AGENTS.md`); `uv.lock` committed alongside
- **Add** `probe/.env.example` for `LITELLM_BASE_URL`, `LITELLM_API_KEY`, `CHAT_MODEL`, `SYSTEM_PROMPT`, TLS cert/key paths, `HOST`, `PORT`, `LOG_LEVEL`
- **No changes** to `bridge-server/` or `glasses-app/` — this spike is fully isolated and disposable

## Capabilities

### New Capabilities
- `byoa-probe`: Temporary diagnostic HTTP server that mimics the BYOA protocol surface, captures raw glasses traffic, forwards to LiteLLM, and returns OpenAI chat-completion responses — for the sole purpose of resolving protocol unknowns before the production migration.

### Modified Capabilities
<!-- None — this change touches no production code and creates no persistent spec changes. -->

## Impact

- **Code**: Adds new `probe/` directory at repo root; does not modify any existing files.
- **Dependencies**: `fastapi`, `uvicorn[standard]`, `httpx` — same set as `bridge-server/`, pinned in `probe/pyproject.toml` and managed via `uv` per `AGENTS.md`.
- **Runtime**: Temporary local process run during hardware probe sessions. Not containerized, not deployed to docker-compose, not registered with Traefik.
- **Cleanup**: Entire `probe/` directory is disposable after the production BYOA migration is complete. The change should be archived (not synced to main specs) once observations are captured.
- **Security**: Server runs HTTPS using the user's existing local CA cert. Bearer auth is accepted-but-logged (not enforced) during the probe so we can observe what the glasses send. Probe runs only on LAN; not exposed to the internet.
