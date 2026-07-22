# even-g2 — Hermes platform plugin for Even Realities G2

Bridges Even Realities G2 smart glasses to a Hermes Gateway via a persistent
WebSocket. The glasses-app connects to this plugin's WS server; the plugin
translates to/from the Hermes platform-adapter interface, so the glasses get
the same streaming, tools, sessions, and pairing flow as Telegram/Discord.

## Architecture

```
┌─────────────┐   WSS (TLS via Tailscale Serve or user reverse proxy)   ┌──────────────────┐
│  glasses-app│ ─────────── hello + token ────────────────────────────▶│  even-g2 plugin  │
│  (on phone) │ ◀── assistant.delta / tool.start/end / turn.done ───── │  (inside Hermes  │
└─────────────┘    + binary PCM frames (audio.start → audio.stop)      │   Gateway process)│
                                                                   │       │            │
                                                                   │       ▼            │
                                                                   │  Hermes Gateway   │
                                                                   │  (LLM, tools,     │
                                                                   │   sessions)       │
                                                                   └──────────────────┘
```

Inbound: glasses → WS server → `EvenG2Adapter.handle_message()` → gateway
Outbound: gateway → `EvenG2Adapter.send_message/edit_message()` → WS push → glasses

Voice: `audio.start` → glasses stream PCM16 LE 16kHz mono → `audio.stop` →
plugin transcribes (LiteLLM → parakeet → faster-whisper fallback) → emits
`transcript` frame → forwards to gateway as a voice `MessageEvent`.

## Requirements

- Hermes Gateway host (this plugin runs inside the gateway process)
- Python ≥ 3.11
- [uv](https://docs.astral.sh/uv/) for env/dep management (per repo `AGENTS.md`)
- Tailscale (recommended) OR a user-provided reverse proxy for TLS

## Install

> **Required: install Python dependencies into the gateway env first.**
>
> The Hermes Gateway loads plugins by source path into its existing Python env
> and does NOT resolve `pyproject.toml` dependencies automatically
> ([docs](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins#lazy-install-optional-python-dependencies)).
> Any runtime dep listed in `pyproject.toml` `[project.dependencies]` must be
> installed manually into the gateway's Python env before enabling the plugin:
>
> ```bash
> # Find the gateway's Python env:
> ls /opt/hermes/.venv/bin/python 2>/dev/null && GW_PY=/opt/hermes/.venv/bin/python || GW_PY=python3
>
> # Install all runtime deps from the plugin's pyproject.toml:
> $GW_PY -m pip install -e /opt/data/plugins/even-g2/
> # Or install individual deps:
> $GW_PY -m pip install 'protobuf>=7.35.1'
> ```
>
> Re-run whenever `[project.dependencies]` in `plugin/pyproject.toml` changes.

```bash
# From GitHub — the /plugin suffix tells Hermes to install from the plugin/ subdirectory
hermes plugins install youruser/even-g2-hermes-bridge/plugin --enable

# Update to latest after pushing changes
hermes plugins update even-g2

# Restart the gateway to load the plugin
hermes gateway restart
```

Or from a local clone:

```bash
# From the repo root:
cd plugin/
uv sync                                    # creates .venv, installs deps
uv build                                   # produces wheel + sdist in dist/

# Install into a Hermes Gateway host:
hermes plugins install ./plugin/
hermes plugins enable even-g2
hermes gateway restart
```

## First-run setup

```bash
hermes even-g2 setup
```

This command will:

1. Generate `EVEN_G2_BRIDGE_TOKEN` if missing and write it to `~/.hermes/.env`.
2. Bind the WS server to loopback (`127.0.0.1:8767`) per the Tailscale pattern.
3. If Tailscale is detected, run `tailscale serve --https=8443 --bg http://127.0.0.1:8767`
   and set `EVEN_G2_BRIDGE_PUBLIC_URL` from Tailscale MagicDNS.
4. If Tailscale is NOT detected, print clear instructions for setting
   `EVEN_G2_BRIDGE_PUBLIC_URL` and configuring a reverse proxy. Does NOT fail.

Other CLI commands:

```bash
hermes even-g2 qr       # print QR code to terminal + write ~/.hermes/even-g2_qr.png
hermes even-g2 url      # print the advertised WSS URL only
```

The glasses-app bootstrap scans the QR (or accepts manual entry) to learn the
WSS URL + token.

## Configuration

All settings are env-var-driven. See `.env.example` for the full list with
defaults. The essential ones:

| Env var | Purpose | Default |
|---|---|---|
| `EVEN_G2_BRIDGE_TOKEN` | Auth token the glasses-app must present in `hello` | (none — `setup` generates one) |
| `EVEN_G2_BRIDGE_HOST` | Bind host | `127.0.0.1` |
| `EVEN_G2_BRIDGE_PORT` | Bind port | `8767` |
| `EVEN_G2_BRIDGE_NET` | Exposure mode: `tailscale`, `reverse-proxy`, or `lan` | `tailscale` |
| `EVEN_G2_BRIDGE_PUBLIC_URL` | Override the auto-detected advertised URL | (auto) |
| `EVEN_G2_ASR_LITELLM_MODEL` | LiteLLM model name for Whisper ASR | (empty → CPU fallback) |
| `EVEN_G2_ASR_LITELLM_BASE_URL` | LiteLLM base URL | (falls back to `LITELLM_BASE_URL`) |
| `EVEN_G2_ASR_LITELLM_API_KEY` | LiteLLM API key | (falls back to `LITELLM_API_KEY`) |
| `EVEN_G2_ALLOWED_USERS` | Comma-separated ACL | (empty → allow all) |

The `setup` command writes resolved values to `~/.hermes/.env`; Hermes loads
that file automatically on gateway start.

## Deployment patterns

### Pattern A: Tailscale Serve (recommended)

The plugin binds to loopback and [Tailscale Serve](https://tailscale.com/kb/1312/serve)
exposes it as a private `wss://` endpoint on your tailnet. No cert management.

```bash
hermes even-g2 setup
# → detects Tailscale, runs `tailscale serve`, sets EVEN_G2_BRIDGE_PUBLIC_URL
```

The glasses-app connects to `wss://<hostname>.<tailnet-name>/`. DNS + TLS + ACL
all flow through Tailscale.

### Pattern B: User-provided reverse proxy

Set `EVEN_G2_BRIDGE_NET=reverse-proxy` and `EVEN_G2_BRIDGE_HOST=127.0.0.1`,
then forward traffic from your own TLS terminator. Example configs:

**nginx** (TLS termination + WebSocket upgrade):

```nginx
server {
    listen 443 ssl http2;
    server_name hermes.example.com;

    ssl_certificate     /etc/letsencrypt/live/hermes.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/hermes.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8767;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 3600s;  # WS keepalive defeats the default 60s
    }
}
```

**Caddy** (automatic HTTPS via Let's Encrypt):

```caddy
hermes.example.com {
    reverse_proxy 127.0.0.1:8767
}
```

Then set `EVEN_G2_BRIDGE_PUBLIC_URL=wss://hermes.example.com` so the QR code
and CLI advertise the right endpoint.

### Pattern C: LAN (development only)

`EVEN_G2_BRIDGE_NET=lan` + `EVEN_G2_BRIDGE_HOST=0.0.0.0` serves plaintext WS
directly on the LAN. No TLS — **do not use in production**. Useful for
development with the glasses-app on the same network.

## Development

```bash
cd plugin/
uv sync                              # installs deps + dev tools
uv run pytest                        # 123 tests across 6 files
uv run ruff check src/ tests/        # lint
uv run basedpyright                  # type check
```

### Regenerating the wire-protocol stubs

Both the Python stubs (`plugin/src/byoa_plugin/proto_gen/hermes_bridge_pb2.py`) and the TypeScript stubs (`glasses-app/src/proto_gen/hermes_bridge.ts`) are generated from `plugin/proto/hermes_bridge.proto` via [buf](https://buf.build). Regenerate after changing the `.proto`:

```bash
task proto
```

CI catches stale stubs via `task proto-check`. Commit the regenerated files alongside the `.proto` change.

## BYOA Setup (Even's Add Agent)

The plugin serves a BYOA-compatible HTTPS endpoint alongside the WS server on the same port. This lets you use Even Realities' built-in "Add Agent" mode (which uses Even's on-device ASR for privacy) alongside the custom G2 app.

### Configuration

1. Set `BYOA_TOKEN` in your plugin environment (separate from `EVEN_G2_BRIDGE_TOKEN`):
   ```bash
   BYOA_TOKEN=your-byoa-secret
   ```

2. Configure Even's Add Agent on your phone:
   - **Agent URL**: `https://<your-plugin-host>:<port>/v1/chat/completions`
   - **Token**: the value of `BYOA_TOKEN`

3. Restart the plugin (or Hermes Gateway).

### How it works

When you say "Hey Even", Even's Add Agent transcribes your speech on-device and POSTs the text to the plugin's BYOA endpoint. The plugin:

1. Creates/looks up a Hermes session for the `even-add-agent` chat_id
2. Pushes an `active` frame to any connected G2 app (prepping the display)
3. Forwards the transcribed text to the LLM via the Hermes Gateway

**Fast responses** (<first-delta latency): the LLM finishes before any streaming frame would have been pushed. The G2 app stays asleep. The Even overlay shows the response.

**Slow responses** (>first-delta latency): the LLM streams at least one `assistant_delta` frame to the G2 app. The G2 app's existing `maybeBringToFront` logic activates it, and the user reads the streaming response there. The Even overlay eventually receives the chat-completion JSON too (late but correct).

No explicit timer — the latency itself decides which surface the user sees.

## Known limitations (v1)

- **Single-user history**: chat_id keyed by device serial. Multiple users on
  the same tailnet get separate threads, but there's no multi-tenant ACL on
  chat_id itself.
- **Parakeet ASR**: requires a signed Swift sidecar binary; only used when
  `EVEN_G2_ASR_SIDECAR_BIN` is set. Not exercised on Linux deployments.
- **No HUD error-message tuning**: plugin emits standard `error` frames; the
  glasses-app renders whatever it renders for them.

## File layout

```
plugin/
├── pyproject.toml          # uv_build, deps, ruff (Google docstring convention)
├── uv.lock
├── .env.example
├── src/byoa_plugin/
│   ├── __init__.py         # register(ctx) entry point
│   ├── adapter.py          # EvenG2Adapter (Hermes platform adapter)
│   ├── config.py           # BridgeConfig.from_env()
│   ├── connections.py      # ConnectionRegistry + StreamState
│   ├── hooks.py            # pre/post_tool_call → tool.start/end frames
│   ├── http_endpoints.py   # /health + /qr multiplexed on the WS port
│   ├── net.py              # resolve_advertised_url (Tailscale → LAN fallback)
│   ├── qr_setup.py         # QR PNG + terminal rendering
│   ├── server.py           # WS server (handshake, dispatch, keepalive)
│   ├── setup_flow.py       # `hermes even-g2 setup`
│   ├── cli.py              # `hermes even-g2 qr|url|setup`
│   ├── wire.py             # frame constructors + parse_frame (Protobuf)
│   └── asr/                # LiteLLM → parakeet → whisper-tiny fallback chain
└── tests/                  # pytest; covers wire, connections, integration
                            # http_endpoints, config, stream_state, tool_label
```
