import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import truststore

truststore.inject_into_ssl()

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "")
CHAT_MODEL = os.getenv("CHAT_MODEL", "")
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT", "")
SSL_CERT_FILE = os.getenv("SSL_CERT_FILE", "")
SSL_KEY_FILE = os.getenv("SSL_KEY_FILE", "")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8765"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
PROBE_LOG_PATH = Path("probe.log")


def _fail(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(1)


if not LITELLM_BASE_URL:
    _fail(f"Refusing to start: LITELLM_BASE_URL is required. Got {LITELLM_BASE_URL!r}")
if not LITELLM_API_KEY:
    _fail("Refusing to start: LITELLM_API_KEY is required.")
if not CHAT_MODEL:
    _fail(f"Refusing to start: CHAT_MODEL is required. Got {CHAT_MODEL!r}")
if not SSL_CERT_FILE or not SSL_KEY_FILE:
    _fail(
        "Refusing to start: SSL_CERT_FILE and SSL_KEY_FILE must both be set for HTTPS. "
        f"Got cert={SSL_CERT_FILE!r}, key={SSL_KEY_FILE!r}"
    )
if not Path(SSL_CERT_FILE).exists():
    _fail(f"Refusing to start: SSL_CERT_FILE path {SSL_CERT_FILE!r} does not exist.")
if not Path(SSL_KEY_FILE).exists():
    _fail(f"Refusing to start: SSL_KEY_FILE path {SSL_KEY_FILE!r} does not exist.")


_turn_counter = 0


def _next_turn() -> int:
    global _turn_counter
    _turn_counter += 1
    return _turn_counter


def _write_log(entry: str) -> None:
    print(entry)
    with PROBE_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(entry + "\n")


def _format_headers(headers: dict[str, str]) -> str:
    return "\n".join(f"  {k}: {v}" for k, v in headers.items())


def _preview(value: Any, limit: int = 200) -> str:
    if isinstance(value, str):
        return value[:limit]
    s = json.dumps(value, default=str)
    return s[:limit]


def _format_messages_summary(messages: list) -> str:
    if not messages:
        return "    (none)"
    lines = []
    for i, m in enumerate(messages):
        role = m.get("role", "?")
        content = _preview(m.get("content", ""))
        lines.append(f"    [{i}] role: {role}, content: {content}")
    return "\n".join(lines)


def _format_parsed_body(body: dict) -> str:
    model = body.get("model", "<missing>")
    user_field = body.get("user", "<absent>")
    messages = body.get("messages", [])
    known_keys = {"model", "user", "messages"}
    other = {k: v for k, v in body.items() if k not in known_keys}
    lines = [
        f"  model: {model!r}",
        f"  user: {user_field!r}",
        "  messages:",
        _format_messages_summary(messages),
    ]
    if other:
        lines.append("  other_fields:")
        for k, v in other.items():
            lines.append(f"    {k}: {_preview(v)}")
    return "\n".join(lines)


def _build_chat_completion(content: str) -> dict:
    return {
        "id": f"g2-probe-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "g2-probe",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": len(content),
            "total_tokens": len(content),
        },
    }


def _build_forward_body(incoming: dict) -> dict:
    messages_in = incoming.get("messages", [])
    forward_messages: list[dict] = []
    has_system = any(isinstance(m, dict) and m.get("role") == "system" for m in messages_in)
    if not has_system and SYSTEM_PROMPT:
        forward_messages.append({"role": "system", "content": SYSTEM_PROMPT})
    forward_messages.extend(messages_in)
    return {
        "model": CHAT_MODEL,
        "messages": forward_messages,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False}
    }


async def _call_litellm(forward_body: dict) -> tuple[int, str, str | None]:
    url = f"{LITELLM_BASE_URL.rstrip('/')}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {LITELLM_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=headers, json=forward_body)
        status = resp.status_code
        if status >= 400:
            return status, "", resp.text[:200]
        try:
            data = resp.json()
        except json.JSONDecodeError:
            return status, "", "non-JSON response"
        choices = data.get("choices") or []
        if not choices:
            return status, "", "no choices in response"
        content = choices[0].get("message", {}).get("content", "")
        return status, content, None
    except httpx.HTTPError as e:
        return 0, "", str(e)[:200]


app = FastAPI(title="BYOA Probe", version="0.1.0")


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "mode": "probe",
        "upstream": LITELLM_BASE_URL,
        "chat_model": CHAT_MODEL,
    }


@app.post("/")
async def byoa_root(request: Request):
    turn = _next_turn()
    iso_ts = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.gmtime())
    client_host = request.client.host if request.client else "?"
    client_port = request.client.port if request.client else "?"
    method = request.method
    path = request.url.path
    headers_dict = dict(request.headers)
    raw_bytes = await request.body()
    raw_body = raw_bytes.decode("utf-8", errors="replace")

    try:
        body = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        _write_log(
            f"=== TURN {turn} — {iso_ts} ===\n"
            f"CLIENT: {client_host}:{client_port}\n"
            f"METHOD: {method}\n"
            f"PATH: {path}\n"
            f"HEADERS (verbatim):\n{_format_headers(headers_dict)}\n"
            f"BODY (raw):\n  {raw_body}\n"
            f"BODY (parsed):\n  (invalid JSON — not parsed)\n"
            f"LITELLM_REQUEST:\n  (skipped — invalid JSON input)\n"
            f"LITELLM_RESPONSE:\n  status: 0\n  content_chars: 0\n  latency_ms: 0\n"
            f"=== END TURN {turn} ===\n"
        )
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "invalid JSON", "type": "invalid_request_error"}},
        )

    forward_body = _build_forward_body(body)
    t0 = time.perf_counter()
    llm_status, llm_content, llm_error = await _call_litellm(forward_body)
    latency_ms = round((time.perf_counter() - t0) * 1000, 1)

    if llm_error is not None:
        if llm_status == 0:
            content_to_glasses = f"[probe] LiteLLM error: {llm_error}"
        elif llm_error == "non-JSON response":
            content_to_glasses = "[probe] LiteLLM returned non-JSON response"
        else:
            content_to_glasses = f"[probe] LiteLLM error: {llm_status} {llm_error[:120]}"
    else:
        content_to_glasses = llm_content

    _write_log(
        f"=== TURN {turn} — {iso_ts} ===\n"
        f"CLIENT: {client_host}:{client_port}\n"
        f"METHOD: {method}\n"
        f"PATH: {path}\n"
        f"HEADERS (verbatim):\n{_format_headers(headers_dict)}\n"
        f"BODY (raw):\n  {raw_body}\n"
        f"BODY (parsed):\n{_format_parsed_body(body)}\n"
        f"LITELLM_REQUEST:\n  model: {CHAT_MODEL!r}\n  messages_count: {len(forward_body['messages'])}\n"
        f"LITELLM_RESPONSE:\n  status: {llm_status}\n  content_chars: {len(content_to_glasses)}\n  latency_ms: {latency_ms}\n"
        f"=== END TURN {turn} ===\n"
    )

    return _build_chat_completion(content_to_glasses)


if __name__ == "__main__":
    uvicorn.run(
        "byoa_probe.server:app",
        host=HOST,
        port=PORT,
        ssl_certfile=SSL_CERT_FILE,
        ssl_keyfile=SSL_KEY_FILE,
        log_level=LOG_LEVEL.lower(),
    )
