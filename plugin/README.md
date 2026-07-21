# even_g2 — Hermes platform plugin for Even Realities G2

Bridges Even Realities G2 smart glasses to a Hermes Gateway via a persistent
WebSocket. The glasses-app connects to this plugin's WS server; the plugin
translates to/from the Hermes platform-adapter interface, so the glasses get
the same streaming, tools, sessions, and pairing flow as Telegram/Discord.

## Architecture

```
┌─────────────┐   WSS (TLS via Tailscale Serve or user reverse proxy)   ┌──────────────────┐
│  glasses-app│ ─────────── hello + token ────────────────────────────▶│  even_g2 plugin  │
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

```bash
# From the repo root after cloning:
cd plugin/
uv sync                                    # creates .venv, installs deps
uv build                                   # produces wheel + sdist in dist/

# Install into a Hermes Gateway host:
hermes plugins install ./plugin/
hermes plugins enable even_g2
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
hermes even-g2 qr       # print QR code to terminal + write ~/.hermes/even_g2_qr.png
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

### Regenerating the TypeScript protocol module

`glasses-app/src/protocol.ts` is generated from `plugin/src/byoa_plugin/protocol.py`
so the WS wire format has one source of truth. Regenerate after changing frame
schemas:

```bash
cd plugin/
uv run python -m byoa_plugin.protocol_gen > ../glasses-app/src/protocol.ts
```

Commit the regenerated file alongside the Python change.

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
│   ├── protocol.py         # frame schemas + constructors + parse_client
│   ├── protocol_gen.py     # generates glasses-app/src/protocol.ts
│   ├── qr_setup.py         # QR PNG + terminal rendering
│   ├── server.py           # WS server (handshake, dispatch, keepalive)
│   ├── setup_flow.py       # `hermes even-g2 setup`
│   ├── cli.py              # `hermes even-g2 qr|url|setup`
│   └── asr/                # LiteLLM → parakeet → whisper-tiny fallback chain
└── tests/                  # pytest; 123 tests across protocol, connections,
                            # http_endpoints, config, stream_state, tool_label
```
