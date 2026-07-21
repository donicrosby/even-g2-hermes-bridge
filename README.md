# even-g2-hermes-bridge

Bridge your Even Realities G2 smart glasses to Hermes Agent (via LiteLLM) using the glasses' built-in **Add Agent** / BYOA mode.

## Architecture

```
┌─────────────┐   HTTPS (TLS terminated by Traefik)   ┌──────────────────┐
│  Even Hub   │ ────────── POST / ──────────────────▶ │  bridge-server   │
│  mobile app │   Authorization: Bearer <BYOA_TOKEN>  │  (FastAPI)       │
│  Add Agent  │   {model, messages:[{user,content}]}  │                  │
└─────────────┘                                        │  - Bearer auth   │
       ▲                                               │  - History store │
       │ BLE                                           │  - Dedup cache   │
       │                                               │  - Prewarm       │
┌──────┴───────┐                                       │       │          │
│  G2 glasses  │ ◀── chat-completion reply ─────────  │       ▼          │
│  (mic + HUD) │                                       │  LiteLLM upstream│
└──────────────┘                                       └──────────────────┘
```

- **bridge-server/** — Python FastAPI HTTP server speaking the BYOA protocol: receives OpenAI chat-completion requests from the glasses' built-in Add Agent mode, manages conversation history server-side (the glasses don't send history), deduplicates parallel duplicate requests the glasses fire per utterance, forwards to LiteLLM
- **glasses-app/** — Legacy custom WebSocket plugin (VAD + Whisper STT + token streaming). Kept as a fallback; not required for the BYOA path. A future change can delete it once BYOA is confirmed stable in production
- **docker-compose.yml** — Docker Compose stack that plugs into existing Traefik

## Quick Start

```bash
cp .env.example .env
# Edit .env: set BYOA_TOKEN, LITELLM_API_KEY, CHAT_MODEL
docker compose up -d --build
```

The bridge serves plaintext HTTP on port 8765. Traefik terminates TLS at `https://hermes.local`.

## Configure the glasses

On your phone, open the **Even** app → **Settings** → **Add Agent** and create an entry:

| Field | Value |
|---|---|
| Name | `Hermes` (or anything you like) |
| URL | `https://hermes.local` (your `BRIDGE_HOST`) |
| Token | The exact value of `BYOA_TOKEN` from your `.env` |

The glasses will POST to this URL when you long-press the touchbar and speak. The bridge authenticates the request, deduplicates the parallel duplicate requests the glasses fire, forwards to LiteLLM with your conversation history, and returns an OpenAI chat-completion JSON response that the glasses render on the HUD.

## Conversation history

The glasses send only the current user message — no history. The bridge maintains history server-side keyed by client IP (so each pair of glasses on your network has its own conversation thread).

- **`/clear`** — say "slash clear" to wipe your conversation history. The bridge clears your client IP's history and returns a confirmation message without calling LiteLLM
- **`MAX_HISTORY_TURNS=10`** — env var; oldest complete user/assistant turns are discarded when the cap is exceeded
- History is in-memory; restarting the container starts fresh conversations

## TLS Behavior

The container serves plaintext HTTP on port 8765 by default — TLS is terminated by Traefik. Set `SSL_CERT_FILE` and `SSL_KEY_FILE` if you want uvicorn to terminate TLS itself (e.g., for direct LAN testing without Traefik).

| SSL_CERT_FILE | SSL_KEY_FILE | Container listens on |
|---|---|---|
| unset | unset | `http://0.0.0.0:8765` (default; Traefik does HTTPS) |
| set | set | `https://0.0.0.0:8765` (uvicorn terminates TLS) |

## Upstream TLS to LiteLLM

The bridge uses Python's `truststore` package, which makes `ssl` use the OS trust store. As long as your LiteLLM's CA is installed in the host's system trust store (or you're using a publicly-trusted cert via Tailscale MagicDNS / Let's Encrypt), the bridge will validate LiteLLM's cert automatically — no `SSL_CA_FILE` env var needed.

## Known limitations (v1)

- **Single-user history**: history is keyed by client IP. Multiple users behind the same NAT share a history thread. Acceptable for personal deployment; flagged for future work if multi-user matters.
- **Duplicate-request dedup window**: glasses fire parallel duplicate requests per utterance. The bridge deduplicates by client IP + content hash within `DEDUP_WINDOW_SECONDS` (default 5 s). If you legitimately say the same thing twice within the window, the second utterance returns the cached reply.
- **No SSE streaming in v1**: probe couldn't confirm glasses accept `text/event-stream`. v1 returns a single non-streaming chat-completion JSON blob. First-token latency = full LLM latency.
- **No HUD error-message tuning**: probe didn't pin down what the HUD shows for 401/500/timeout. The bridge returns standard OpenAI-style error shapes; the glasses render whatever they render.
- **Cold-start latency**: probe measured 17.3 s cold-start on the first LiteLLM call after server boot. The bridge fires a `prewarm` request on startup to absorb this cost off the first user utterance.
- **`glasses-app/` not deleted**: the legacy WebSocket plugin still works if deployed alongside the old `bridge-server`. The new BYOA path obsoletes it; a follow-up cleanup change can delete `glasses-app/` and `probe/` once BYOA is confirmed stable.

## Directory Layout

```
.
├── bridge-server/
│   ├── pyproject.toml
│   ├── uv.lock
│   ├── Dockerfile
│   ├── .dockerignore
│   ├── .env.example
│   └── src/byoa_bridge/
│       ├── __init__.py
│       └── server.py
├── glasses-app/              # legacy custom plugin (kept for fallback)
├── probe/                    # disposable probe server from the BYOA spike
├── docker-compose.yml
├── .env.example
├── AGENTS.md                 # repo Python tooling policy (uv + pyproject.toml)
└── README.md
```

## Python tooling

This repo uses **[uv](https://docs.astral.sh/uv/)** for Python environment and dependency management. See `AGENTS.md` for the full policy. To run the bridge locally without Docker:

```bash
cd bridge-server/
uv sync
uv run --env-file .env uvicorn byoa_bridge.server:app --host 0.0.0.0 --port 8765
```
