import asyncio
import hashlib
import hmac
import json
import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Optional

import truststore

truststore.inject_into_ssl()

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "").rstrip("/")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "")
CHAT_MODEL = os.getenv("CHAT_MODEL", "")
BYOA_TOKEN = os.getenv("BYOA_TOKEN", "")
SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are Hermes Agent, a helpful AI assistant accessible through Even Realities G2 smart glasses. "
    "Keep responses concise, natural, and easy to read on a small micro-LED display.",
)
MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "10"))
DEDUP_WINDOW_SECONDS = int(os.getenv("DEDUP_WINDOW_SECONDS", "5"))
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8765"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
SSL_CERT_FILE = os.getenv("SSL_CERT_FILE", "")
SSL_KEY_FILE = os.getenv("SSL_KEY_FILE", "")


def _fail(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(1)


if not LITELLM_BASE_URL:
    _fail(f"Refusing to start: LITELLM_BASE_URL is required. Got {LITELLM_BASE_URL!r}")
if not LITELLM_API_KEY:
    _fail("Refusing to start: LITELLM_API_KEY is required.")
if not CHAT_MODEL:
    _fail(f"Refusing to start: CHAT_MODEL is required. Got {CHAT_MODEL!r}")
if not BYOA_TOKEN:
    _fail("Refusing to start: BYOA_TOKEN is required.")
if DEDUP_WINDOW_SECONDS < 1:
    _fail(
        f"Refusing to start: DEDUP_WINDOW_SECONDS must be >= 1. Got {DEDUP_WINDOW_SECONDS}"
    )


logging.basicConfig(
    level=LOG_LEVEL,
    stream=sys.stdout,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
LOG = logging.getLogger("byoa_bridge")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _preview(s: str, limit: int = 120) -> str:
    return s if len(s) <= limit else s[:limit] + "..."


def _latest_user_content(body: dict[str, Any]) -> Optional[str]:
    messages = body.get("messages") or []
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            content = m.get("content")
            if isinstance(content, str) and content.strip():
                return content
    return None


def _build_forward_messages(history: list[dict], user_content: str) -> list[dict]:
    forwarded = [{"role": "system", "content": SYSTEM_PROMPT}]
    forwarded.extend(history)
    forwarded.append({"role": "user", "content": user_content})
    return forwarded


def _chat_completion(content: str) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-byoa-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "byoa-bridge",
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


def _openai_error(message: str, etype: str) -> dict[str, Any]:
    return {"error": {"message": message, "type": etype}}


HISTORIES: dict[str, list[dict]] = {}


def _history_for(client_ip: str) -> list[dict]:
    return HISTORIES.setdefault(client_ip, [])


def _history_append(client_ip: str, user_content: str, assistant_content: str) -> int:
    history = _history_for(client_ip)
    history.append({"role": "user", "content": user_content})
    history.append({"role": "assistant", "content": assistant_content})
    while len(history) > MAX_HISTORY_TURNS * 2:
        history.pop(0)
        history.pop(0)
    return len(history) // 2


def _history_clear(client_ip: str) -> None:
    HISTORIES.pop(client_ip, None)


INFLIGHT_LOCK = asyncio.Lock()
INFLIGHT: dict[str, asyncio.Task] = {}
RECENT_CACHE: dict[str, tuple[str, float]] = {}


def _dedup_key(client_ip: str, user_content: str) -> str:
    digest = hashlib.sha256(user_content.encode("utf-8")).hexdigest()
    return f"{client_ip}:{digest[:16]}"


async def _handle_request(
    client_ip: str, user_content: str
) -> tuple[str, str, bool]:
    """Returns (assistant_content, event_name, was_cached_or_deduped).

    event_name is one of: dedup_new, dedup_inflight_hit, dedup_recent_hit.
    was_cached_or_deduped is True for inflight/recent hits, False for new requests.
    """
    key = _dedup_key(client_ip, user_content)
    key_prefix = key.split(":")[-1]

    async with INFLIGHT_LOCK:
        if key in INFLIGHT:
            task = INFLIGHT[key]
            LOG.info(
                "dedup_inflight_hit",
                extra={"event": "dedup_inflight_hit", "client_ip": client_ip, "dedup_key": key_prefix},
            )
            return (await asyncio.shield(task), "dedup_inflight_hit", True)

        now = time.time()
        cached = RECENT_CACHE.get(key)
        if cached and (now - cached[1]) < DEDUP_WINDOW_SECONDS:
            LOG.info(
                "dedup_recent_hit",
                extra={
                    "event": "dedup_recent_hit",
                    "client_ip": client_ip,
                    "dedup_key": key_prefix,
                    "cache_age_sec": round(now - cached[1], 2),
                },
            )
            return (cached[0], "dedup_recent_hit", True)
        if cached:
            RECENT_CACHE.pop(key, None)

        task = asyncio.create_task(_run_litellm(client_ip, user_content, key))
        INFLIGHT[key] = task
        LOG.info(
            "dedup_new",
            extra={"event": "dedup_new", "client_ip": client_ip, "dedup_key": key_prefix},
        )

    try:
        result = await task
    finally:
        async with INFLIGHT_LOCK:
            INFLIGHT.pop(key, None)

    RECENT_CACHE[key] = (result, time.time())
    _cleanup_recent_cache()
    return (result, "dedup_new", False)


def _cleanup_recent_cache() -> None:
    now = time.time()
    expired = [k for k, (_, ts) in RECENT_CACHE.items() if (now - ts) >= DEDUP_WINDOW_SECONDS]
    for k in expired:
        RECENT_CACHE.pop(k, None)


async def _run_litellm(client_ip: str, user_content: str, key: str) -> str:
    if user_content.strip().lower() == "/clear":
        _history_clear(client_ip)
        LOG.info(
            "history_clear",
            extra={"event": "history_clear", "client_ip": client_ip},
        )
        return "Conversation history cleared."

    history = list(_history_for(client_ip))
    forward_messages = _build_forward_messages(history, user_content)
    url = f"{LITELLM_BASE_URL}/v1/chat/completions"
    payload = {
        "model": CHAT_MODEL,
        "messages": forward_messages,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {LITELLM_API_KEY}",
        "Content-Type": "application/json",
    }

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as e:
        LOG.error(
            "litellm_transport_error",
            extra={
                "event": "litellm_error",
                "client_ip": client_ip,
                "error": str(e)[:200],
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            },
        )
        raise

    latency_ms = round((time.perf_counter() - t0) * 1000, 1)

    if resp.status_code >= 400:
        LOG.error(
            "litellm_http_error",
            extra={
                "event": "litellm_error",
                "client_ip": client_ip,
                "status": resp.status_code,
                "body": resp.text[:200],
                "latency_ms": latency_ms,
            },
        )
        raise RuntimeError(f"LiteLLM returned {resp.status_code}: {resp.text[:200]}")

    try:
        data = resp.json()
    except json.JSONDecodeError:
        LOG.error(
            "litellm_non_json",
            extra={
                "event": "litellm_error",
                "client_ip": client_ip,
                "latency_ms": latency_ms,
            },
        )
        raise RuntimeError("LiteLLM returned non-JSON response")

    choices = data.get("choices") or []
    if not choices:
        LOG.error(
            "litellm_no_choices",
            extra={
                "event": "litellm_error",
                "client_ip": client_ip,
                "body": resp.text[:200],
                "latency_ms": latency_ms,
            },
        )
        raise RuntimeError("LiteLLM returned no choices")

    content = choices[0].get("message", {}).get("content", "")
    turns = _history_append(client_ip, user_content, content)
    LOG.info(
        "history_append",
        extra={
            "event": "history_append",
            "client_ip": client_ip,
            "turns": turns,
            "preview": _preview(user_content),
        },
    )
    LOG.info(
        "litellm_complete",
        extra={
            "event": "litellm_complete",
            "client_ip": client_ip,
            "status": resp.status_code,
            "model": CHAT_MODEL,
            "content_chars": len(content),
            "latency_ms": latency_ms,
        },
    )
    return content


async def _prewarm() -> None:
    url = f"{LITELLM_BASE_URL}/v1/chat/completions"
    payload = {
        "model": CHAT_MODEL,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {LITELLM_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.post(url, headers=headers, json=payload)
        LOG.info("prewarm_complete", extra={"event": "prewarm_complete", "model": CHAT_MODEL})
    except Exception as e:
        LOG.warning(
            "prewarm_failed",
            extra={"event": "prewarm_failed", "error": str(e)[:200]},
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    LOG.info(
        "startup",
        extra={
            "event": "startup",
            "mode": "byoa",
            "listen": f"http://{HOST}:{PORT}",
            "upstream": LITELLM_BASE_URL,
            "chat_model": CHAT_MODEL,
            "max_history_turns": MAX_HISTORY_TURNS,
            "dedup_window_sec": DEDUP_WINDOW_SECONDS,
        },
    )
    asyncio.create_task(_prewarm())
    yield
    LOG.info("shutdown", extra={"event": "shutdown"})


app = FastAPI(title="Even G2 BYOA Bridge", version="0.1.0", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=512)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "mode": "byoa",
        "upstream": LITELLM_BASE_URL,
        "chat_model": CHAT_MODEL,
    }


@app.post("/")
async def byoa_root(request: Request):
    client_ip = _client_ip(request)

    auth = request.headers.get("authorization", "")
    expected = f"Bearer {BYOA_TOKEN}"
    if not hmac.compare_digest(auth, expected):
        LOG.warning(
            "auth_rejected",
            extra={"event": "auth_rejected", "client_ip": client_ip},
        )
        return JSONResponse(
            status_code=401,
            content=_openai_error("unauthorized", "auth_error"),
        )

    raw = await request.body()
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        LOG.warning(
            "invalid_json",
            extra={"event": "invalid_json", "client_ip": client_ip},
        )
        return JSONResponse(
            status_code=400,
            content=_openai_error("invalid JSON", "invalid_request_error"),
        )

    user_content = _latest_user_content(body)
    if user_content is None:
        LOG.warning(
            "no_user_message",
            extra={"event": "no_user_message", "client_ip": client_ip},
        )
        return JSONResponse(
            status_code=400,
            content=_openai_error("no user message", "invalid_request_error"),
        )

    try:
        content, event_name, was_deduped = await _handle_request(client_ip, user_content)
    except Exception as e:
        LOG.error(
            "request_failed",
            extra={"event": "request_failed", "client_ip": client_ip, "error": str(e)[:200]},
        )
        return JSONResponse(
            status_code=502,
            content=_openai_error(f"upstream error: {str(e)[:160]}", "upstream_error"),
        )

    return _chat_completion(content)


if __name__ == "__main__":
    uvicorn.run(
        "byoa_bridge.server:app",
        host=HOST,
        port=PORT,
        ssl_certfile=SSL_CERT_FILE or None,
        ssl_keyfile=SSL_KEY_FILE or None,
        log_level=LOG_LEVEL.lower(),
    )
