import asyncio
import json
import os
import sys
import time
from typing import AsyncIterator

import truststore

truststore.inject_into_ssl()

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

SSE_DELAY_CREATED = float(os.getenv("SSE_DELAY_CREATED", "0"))
SSE_DELAY_IN_PROGRESS = float(os.getenv("SSE_DELAY_IN_PROGRESS", "2"))
SSE_DELAY_FIRST_DELTA = float(os.getenv("SSE_DELAY_FIRST_DELTA", "3"))
SSE_DELAY_BETWEEN_DELTAS = float(os.getenv("SSE_DELAY_BETWEEN_DELTAS", "0.5"))
SSE_DELAY_BEFORE_COMPLETED = float(os.getenv("SSE_DELAY_BEFORE_COMPLETED", "0.5"))

CANDIDATE_CONTENT = ["Hello", ", world", ". Testing SSE."]
RESPONSE_ID = "sse-probe-1"

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8766"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
SSL_CERT_FILE = os.getenv("SSL_CERT_FILE", "")
SSL_KEY_FILE = os.getenv("SSL_KEY_FILE", "")


def _fail(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(1)


if not SSL_CERT_FILE or not SSL_KEY_FILE:
    _fail(
        f"Refusing to start: SSL_CERT_FILE and SSL_KEY_FILE must both be set for HTTPS. "
        f"Got cert={SSL_CERT_FILE!r}, key={SSL_KEY_FILE!r}"
    )
if not os.path.exists(SSL_CERT_FILE):
    _fail(f"Refusing to start: SSL_CERT_FILE path {SSL_CERT_FILE!r} does not exist.")
if not os.path.exists(SSL_KEY_FILE):
    _fail(f"Refusing to start: SSL_KEY_FILE path {SSL_KEY_FILE!r} does not exist.")


def _format_headers(headers) -> str:
    return "\n".join(f"  {k}: {v}" for k, v in headers.items())


def _preview(s: str, limit: int = 120) -> str:
    return s if len(s) <= limit else s[:limit] + "..."


def _parse_body_summary(raw: str) -> str:
    if not raw:
        return "(empty)"
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return f"(invalid JSON: {raw[:80]!r})"
    messages = body.get("messages") or []
    lines = [f"  model: {body.get('model', '<missing>')!r}"]
    lines.append(f"  user: {body.get('user', '<absent>')!r}")
    if messages:
        last = messages[-1]
        content = last.get("content", "") if isinstance(last, dict) else ""
        lines.append(f"  latest_message: role={last.get('role')!r}, content={_preview(content)}")
    else:
        lines.append("  messages: (none)")
    return "\n".join(lines)


def _log_request(request: Request, raw: str) -> None:
    client_host = request.client.host if request.client else "?"
    iso_ts = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.gmtime())
    print(
        f"=== POST {request.url.path} — {iso_ts} ===\n"
        f"CLIENT: {client_host}\n"
        f"HEADERS:\n{_format_headers(dict(request.headers))}\n"
        f"BODY (raw): {raw[:200]!r}\n"
        f"BODY (parsed):\n{_parse_body_summary(raw)}\n"
        f"TIMING: created={SSE_DELAY_CREATED}s, in_progress={SSE_DELAY_IN_PROGRESS}s, "
        f"first_delta={SSE_DELAY_FIRST_DELTA}s, between_deltas={SSE_DELAY_BETWEEN_DELTAS}s, "
        f"before_completed={SSE_DELAY_BEFORE_COMPLETED}s\n"
        f"=== END ===",
        flush=True,
    )


SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # defeat nginx buffering if behind a proxy
}


def _sse_event(event_name: str | None, data: dict | str) -> str:
    if isinstance(data, (dict, list)):
        data_str = json.dumps(data)
    else:
        data_str = data
    if event_name:
        return f"event: {event_name}\ndata: {data_str}\n\n"
    return f"data: {data_str}\n\n"


async def _stream_openresponses() -> AsyncIterator[str]:
    if SSE_DELAY_CREATED:
        await asyncio.sleep(SSE_DELAY_CREATED)
    yield _sse_event("response.created", {"id": RESPONSE_ID, "status": "in_progress"})

    if SSE_DELAY_IN_PROGRESS:
        await asyncio.sleep(SSE_DELAY_IN_PROGRESS)
    yield _sse_event("response.in_progress", {"id": RESPONSE_ID, "status": "in_progress"})

    if SSE_DELAY_FIRST_DELTA:
        await asyncio.sleep(SSE_DELAY_FIRST_DELTA)

    for i, chunk in enumerate(CANDIDATE_CONTENT):
        yield _sse_event("response.output_text.delta", {"delta": chunk, "index": i})
        if i < len(CANDIDATE_CONTENT) - 1:
            await asyncio.sleep(SSE_DELAY_BETWEEN_DELTAS)

    if SSE_DELAY_BEFORE_COMPLETED:
        await asyncio.sleep(SSE_DELAY_BEFORE_COMPLETED)
    yield _sse_event("response.completed", {"id": RESPONSE_ID, "status": "completed"})

    yield "data: [DONE]\n\n"


async def _stream_openai_chunk() -> AsyncIterator[str]:
    await asyncio.sleep(SSE_DELAY_CREATED + SSE_DELAY_IN_PROGRESS + SSE_DELAY_FIRST_DELTA)

    for i, chunk in enumerate(CANDIDATE_CONTENT):
        payload = {
            "id": RESPONSE_ID,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": "sse-probe",
            "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}],
        }
        yield _sse_event(None, payload)
        if i < len(CANDIDATE_CONTENT) - 1:
            await asyncio.sleep(SSE_DELAY_BETWEEN_DELTAS)

    await asyncio.sleep(SSE_DELAY_BEFORE_COMPLETED)
    final = {
        "id": RESPONSE_ID,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "sse-probe",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield _sse_event(None, final)
    yield "data: [DONE]\n\n"


async def _stream_raw() -> AsyncIterator[str]:
    await asyncio.sleep(SSE_DELAY_CREATED + SSE_DELAY_IN_PROGRESS + SSE_DELAY_FIRST_DELTA)

    for i, chunk in enumerate(CANDIDATE_CONTENT):
        yield _sse_event(None, chunk)
        if i < len(CANDIDATE_CONTENT) - 1:
            await asyncio.sleep(SSE_DELAY_BETWEEN_DELTAS)

    await asyncio.sleep(SSE_DELAY_BEFORE_COMPLETED)
    yield "data: [DONE]\n\n"


app = FastAPI(title="SSE Tolerance Probe", version="0.1.0")


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "mode": "sse-probe",
        "flavors": ["/openresponses", "/openai-chunk", "/raw"],
        "timing": {
            "created": SSE_DELAY_CREATED,
            "in_progress": SSE_DELAY_IN_PROGRESS,
            "first_delta": SSE_DELAY_FIRST_DELTA,
            "between_deltas": SSE_DELAY_BETWEEN_DELTAS,
            "before_completed": SSE_DELAY_BEFORE_COMPLETED,
        },
    }


async def _handle_sse_post(request: Request, streamer) -> StreamingResponse:
    raw = (await request.body()).decode("utf-8", errors="replace")
    _log_request(request, raw)
    return StreamingResponse(streamer(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/")
async def root_openresponses(request: Request) -> StreamingResponse:
    return await _handle_sse_post(request, _stream_openresponses)


@app.post("/openresponses")
async def post_openresponses(request: Request) -> StreamingResponse:
    return await _handle_sse_post(request, _stream_openresponses)


@app.post("/openai-chunk")
async def post_openai_chunk(request: Request) -> StreamingResponse:
    return await _handle_sse_post(request, _stream_openai_chunk)


@app.post("/raw")
async def post_raw(request: Request) -> StreamingResponse:
    return await _handle_sse_post(request, _stream_raw)


if __name__ == "__main__":
    uvicorn.run(
        "sse_server:app",
        host=HOST,
        port=PORT,
        ssl_certfile=SSL_CERT_FILE,
        ssl_keyfile=SSL_KEY_FILE,
        log_level=LOG_LEVEL.lower(),
    )
