# even-g2-hermes-bridge

Bridge your Even Realities G2 smart glasses to Hermes Agent (via LiteLLM).

## Architecture

- **bridge-server/** — Python FastAPI WebSocket server with WebRTC VAD, Whisper STT, and streaming LLM relay
- **glasses-app/** — TypeScript Even Hub SDK app that streams microphone audio and displays responses
- **docker-compose.yml** — Docker Compose stack that plugs into existing Traefik

## Quick Start

```bash
cp .env.example .env
# Edit .env for your setup
docker compose up -d --build
```

The bridge serves plaintext WebSocket on port 8765. Traefik terminates TLS at `wss://hermes.local/ws/glasses`.

## TLS Behavior

| SSL_CERT_FILE | SSL_KEY_FILE | Server | Upstream httpx |
|---------------|--------------|--------|----------------|
| unset         | unset        | `ws://` plaintext | System CAs |
| unset         | unset        | `ws://` plaintext | Custom root CA if SSL_CA_FILE set |
| set           | set          | `wss://` TLS | System CAs or custom root CA |

## Directory Layout

```
.
├── bridge-server/
│   ├── main.py
│   ├── requirements.txt
│   ├── Dockerfile
│   ├── .dockerignore
│   ├── .env.example
│   └── test-client.html
├── glasses-app/
│   ├── index.html
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   └── src/main.ts
├── docker-compose.yml
├── .env.example
└── certs/
    └── root_ca.crt
```
