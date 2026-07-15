#!/usr/bin/env python3
"""
even-g2-hermes-bridge
Local WebSocket bridge server for Even Realities G2 -> Hermes Agent.
- TLS optional: auto-enabled if you provide a cert+key (standalone mode)
- Plaintext default: intended for use behind Traefik (TLS termination)
- Custom root CA for upstream LiteLLM (if upstream uses internal PKI)
- Receives raw PCM16 16kHz mono audio from glasses over WebSocket
- Runs WebRTC VAD to detect speech start/end
- Sends utterances to your LiteLLM Whisper endpoint
- Streams LLM responses back to glasses as text
"""

import asyncio
import io
import json
import logging
import os
import ssl
import sys
import time
import uuid
import wave
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Optional

import httpx
import uvicorn
import webrtcvad
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pythonjsonlogger.jsonlogger import JsonFormatter

# -- Configuration ---------------------------------------------------------------
LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://localhost:4000")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-1")
CHAT_MODEL = os.getenv("CHAT_MODEL", "claude-sonnet-4")
SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are Hermes Agent, a helpful AI assistant accessible through Even Realities G2 smart glasses. "
    "Keep responses concise, natural, and easy to read on a small micro-LED display. "
    "Use short paragraphs and bullet points when appropriate.",
)

VAD_AGGRESSIVENESS = int(os.getenv("VAD_AGGRESSIVENESS", "3"))
SILENCE_FRAMES = int(os.getenv("SILENCE_FRAMES", "30"))
MIN_SPEECH_FRAMES = int(os.getenv("MIN_SPEECH_FRAMES", "5"))
LOOKBACK_FRAMES = int(os.getenv("LOOKBACK_FRAMES", "10"))
MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "10"))

SAMPLE_RATE = 16000
FRAME_DURATION_MS = 30
BYTES_PER_FRAME = int(SAMPLE_RATE * FRAME_DURATION_MS / 1000 * 2)

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8765"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# -- TLS / Certs -----------------------------------------------------------------
SSL_CERT_FILE = os.getenv("SSL_CERT_FILE", "")
SSL_KEY_FILE = os.getenv("SSL_KEY_FILE", "")
SSL_CA_FILE = os.getenv("SSL_CA_FILE", "")

# -- Logging Setup ---------------------------------------------------------------
def _setup_logging(level: str) -> logging.Logger:
    log = logging.getLogger("hermes_bridge")
    log.setLevel(level)
    if not log.handlers:
        handler = logging.StreamHandler(sys.stdout)
        if sys.stdout.isatty():
            fmt = "%(asctime)s | %(levelname)-8s | %(session_id)s | %(message)s"
            handler.setFormatter(logging.Formatter(fmt, datefmt="%H:%M:%S.%f")[:-3])
        else:
            fmt = "%(asctime)s %(levelname)s %(name)s %(session_id)s %(message)s"
            handler.setFormatter(JsonFormatter(fmt))
        log.addHandler(handler)
    return log

LOG = _setup_logging(LOG_LEVEL)


def _build_server_ssl_ctx() -> Optional[ssl.SSLContext]:
    has_cert = bool(SSL_CERT_FILE)
    has_key = bool(SSL_KEY_FILE)
    if not has_cert and not has_key:
        return None
    if has_cert != has_key:
        raise RuntimeError(
            "Both SSL_CERT_FILE and SSL_KEY_FILE must be set together, or both omitted. "
            f"Got cert={SSL_CERT_FILE!r}, key={SSL_KEY_FILE!r}"
        )
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(SSL_CERT_FILE, SSL_KEY_FILE)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    LOG.info("Server TLS enabled", extra={"event": "tls_config", "cert_file": SSL_CERT_FILE})
    return ctx


def _build_client_ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if SSL_CA_FILE:
        ctx.load_verify_locations(SSL_CA_FILE)
        LOG.info("Upstream SSL: loaded custom root CA", extra={"event": "tls_config", "ca_file": SSL_CA_FILE})
    return ctx

CLIENT_SSL_CTX = _build_client_ssl_ctx()


# -- Session ----------------------------------------------------------------------
@dataclass
class Session:
    ws: WebSocket
    sid: str
    log: logging.LoggerAdapter
    vad: webrtcvad.Vad
    state: str = "idle"
    speech_frames: int = 0
    silence_frames: int = 0
    utterance_buffer: bytearray = field(default_factory=bytearray)
    leftover_audio: bytearray = field(default_factory=bytearray)
    lookback: deque = field(default_factory=lambda: deque(maxlen=LOOKBACK_FRAMES))
    history: list = field(default_factory=list)
    task: Optional[asyncio.Task] = None
    total_bytes_rx: int = 0
    utterance_count: int = 0
    stt_latencies: list = field(default_factory=list)
    llm_ttfts: list = field(default_factory=list)
    llm_total_latencies: list = field(default_factory=list)
    connected_at: float = field(default_factory=time.time)
    last_activity_at: float = field(default_factory=time.time)

    def bump_activity(self):
        self.last_activity_at = time.time()


def pcm16_to_wav(pcm_data: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return buf.getvalue()


def handle_audio_chunk(session: Session, data: bytes):
    session.total_bytes_rx += len(data)
    session.leftover_audio.extend(data)
    while len(session.leftover_audio) >= BYTES_PER_FRAME:
        frame = bytes(session.leftover_audio[:BYTES_PER_FRAME])
        session.leftover_audio = session.leftover_audio[BYTES_PER_FRAME:]
        try:
            is_speech = session.vad.is_speech(frame, SAMPLE_RATE)
        except Exception:
            continue
        if session.state == "idle":
            session.lookback.append(frame)
            if is_speech:
                session.speech_frames += 1
                if session.speech_frames >= MIN_SPEECH_FRAMES:
                    session.state = "speech"
                    for f in session.lookback:
                        session.utterance_buffer.extend(f)
                    session.utterance_buffer.extend(frame)
                    session.lookback.clear()
                    session.speech_frames = 0
            else:
                session.speech_frames = 0
        elif session.state == "speech":
            session.utterance_buffer.extend(frame)
            if is_speech:
                session.silence_frames = 0
            else:
                session.silence_frames += 1
                if session.silence_frames >= SILENCE_FRAMES:
                    session.state = "processing"
                    session.log.info("VAD: speech->processing", extra={"event": "vad_transition", "utterance_bytes": len(session.utterance_buffer)})
                    session.task = asyncio.create_task(process_utterance(session), name=f"utterance-{session.sid}")
                    return
        elif session.state == "processing":
            pass


def reset_session(session: Session):
    session.state = "idle"
    session.speech_frames = 0
    session.silence_frames = 0
    session.utterance_buffer = bytearray()
    session.leftover_audio = bytearray()
    session.lookback.clear()


async def transcribe(audio_wav: bytes, session: Session) -> str:
    url = f"{LITELLM_BASE_URL}/v1/audio/transcriptions"
    files = {"file": ("audio.wav", audio_wav, "audio/wav")}
    data = {"model": WHISPER_MODEL}
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=30.0, verify=CLIENT_SSL_CTX) as client:
            resp = await client.post(url, data=data, files=files)
            resp.raise_for_status()
            result = resp.json()
    except Exception as e:
        session.log.error("STT failed", extra={"event": "stt_error", "error": str(e), "latency_ms": round((time.perf_counter() - t0) * 1000, 1)})
        raise
    latency = (time.perf_counter() - t0) * 1000
    session.stt_latencies.append(latency)
    transcript = result.get("text", "")
    session.log.info("STT complete", extra={"event": "stt_complete", "latency_ms": round(latency, 1), "transcript": transcript[:200]})
    return transcript


async def chat_stream(transcript: str, session: Session) -> str:
    url = f"{LITELLM_BASE_URL}/v1/chat/completions"
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(session.history)
    messages.append({"role": "user", "content": transcript})
    payload = {"model": CHAT_MODEL, "messages": messages, "stream": True, "max_tokens": 512}
    accumulated = ""
    t0 = time.perf_counter()
    first_token_time: Optional[float] = None
    try:
        async with httpx.AsyncClient(timeout=60.0, verify=CLIENT_SSL_CTX) as client:
            async with client.stream("POST", url, json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    json_str = line[5:].strip()
                    if json_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(json_str)
                    except json.JSONDecodeError:
                        continue
                    choice = chunk["choices"][0]
                    if choice.get("finish_reason"):
                        break
                    content = choice["delta"].get("content", "")
                    if not content:
                        continue
                    if first_token_time is None:
                        first_token_time = time.perf_counter()
                        ttft = (first_token_time - t0) * 1000
                        session.llm_ttfts.append(ttft)
                        session.log.info("LLM first token", extra={"event": "llm_ttft", "latency_ms": round(ttft, 1)})
                    accumulated += content
                    session.bump_activity()
                    await session.ws.send_json({"type": "text", "content": accumulated})
    except Exception as e:
        session.log.error("LLM failed", extra={"event": "llm_error", "error": str(e)})
        raise
    total = (time.perf_counter() - t0) * 1000
    session.llm_total_latencies.append(total)
    session.log.info("LLM complete", extra={"event": "llm_complete", "total_latency_ms": round(total, 1), "tokens_est": len(accumulated) // 4})
    return accumulated


async def process_utterance(session: Session):
    if len(session.utterance_buffer) < BYTES_PER_FRAME * MIN_SPEECH_FRAMES:
        session.log.warning("Utterance too short; dropping")
        reset_session(session)
        return
    wav_bytes = pcm16_to_wav(bytes(session.utterance_buffer))
    try:
        await session.ws.send_json({"type": "status", "title": "Processing", "content": "Transcribing..."})
        transcript = await transcribe(wav_bytes, session)
        session.utterance_count += 1
        if not transcript.strip():
            await session.ws.send_json({"type": "status", "title": "Hermes", "content": "(didn't catch that -- try again)"})
            reset_session(session)
            return
        await session.ws.send_json({"type": "status", "title": "You said", "content": transcript})
        response_text = await chat_stream(transcript, session)
        session.history.append({"role": "user", "content": transcript})
        session.history.append({"role": "assistant", "content": response_text})
        while len(session.history) > MAX_HISTORY_TURNS * 2:
            session.history.pop(0)
            session.history.pop(0)
    except Exception as e:
        session.log.error("Pipeline error", extra={"event": "pipeline_error", "error": str(e)})
        await session.ws.send_json({"type": "status", "title": "Error", "content": str(e)[:120]})
    finally:
        reset_session(session)


async def session_loop(session: Session):
    session.log.info("Glasses connected", extra={"event": "connect", "client": str(session.ws.client)})
    await session.ws.send_json({"type": "status", "title": "Listening", "content": "Speak to talk with Hermes"})
    try:
        while True:
            message = await session.ws.receive()
            msg_type = message.get("type")
            if msg_type == "websocket.disconnect":
                break
            if msg_type == "websocket.receive":
                if "bytes" in message:
                    data = message["bytes"]
                    session.bump_activity()
                    if session.state == "processing" and session.task:
                        continue
                    handle_audio_chunk(session, data)
                    if session.state == "processing" and session.task:
                        await session.task
                elif "text" in message:
                    try:
                        cmd = json.loads(message["text"])
                        session.log.info("Control message", extra={"event": "control", "cmd": cmd})
                        if cmd.get("action") == "clear":
                            session.history.clear()
                            await session.ws.send_json({"type": "status", "title": "Cleared", "content": "Conversation history reset"})
                    except json.JSONDecodeError:
                        session.log.warning("Invalid control JSON", extra={"raw": message["text"][:80]})
    except WebSocketDisconnect:
        session.log.info("Glasses disconnected", extra={"event": "disconnect"})
    except Exception as e:
        session.log.error("Session error", extra={"event": "session_error", "error": str(e)})
    finally:
        if session.task and not session.task.done():
            session.task.cancel()
            try:
                await session.task
            except asyncio.CancelledError:
                pass
        duration = time.time() - session.connected_at
        session.log.info("Session summary", extra={
            "event": "session_summary",
            "duration_sec": round(duration, 1),
            "total_bytes_rx": session.total_bytes_rx,
            "utterance_count": session.utterance_count,
            "avg_stt_ms": round(sum(session.stt_latencies) / len(session.stt_latencies), 1) if session.stt_latencies else 0,
            "avg_llm_ttft_ms": round(sum(session.llm_ttfts) / len(session.llm_ttfts), 1) if session.llm_ttfts else 0,
            "avg_llm_total_ms": round(sum(session.llm_total_latencies) / len(session.llm_total_latencies), 1) if session.llm_total_latencies else 0,
        })


@asynccontextmanager
async def lifespan(app: FastAPI):
    ssl_mode = "TLS" if (SSL_CERT_FILE and SSL_KEY_FILE) else "plaintext"
    LOG.info("Bridge starting", extra={"event": "startup", "mode": ssl_mode, "stt_endpoint": f"{LITELLM_BASE_URL}/v1/audio/transcriptions", "llm_model": CHAT_MODEL, "listen_url": f"{'wss' if SSL_CERT_FILE else 'ws'}://{HOST}:{PORT}/ws/glasses"})
    yield
    LOG.info("Bridge shutting down", extra={"event": "shutdown"})


app = FastAPI(title="Even G2 Hermes Bridge", lifespan=lifespan)

@app.get("/")
async def root():
    return FileResponse("test-client.html")

@app.get("/health")
async def health():
    return {"status": "ok", "server_tls": bool(SSL_CERT_FILE and SSL_KEY_FILE), "upstream_ca": bool(SSL_CA_FILE)}

@app.websocket("/ws/glasses")
async def glasses_ws(ws: WebSocket):
    await ws.accept()
    sid = str(uuid.uuid4())[:8]
    log = logging.LoggerAdapter(LOG, {"session_id": sid})
    session = Session(ws=ws, sid=sid, log=log, vad=webrtcvad.Vad(VAD_AGGRESSIVENESS))
    try:
        await session_loop(session)
    finally:
        pass


if __name__ == "__main__":
    ssl_ctx = _build_server_ssl_ctx()
    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        ssl_keyfile=SSL_KEY_FILE if SSL_CERT_FILE else None,
        ssl_certfile=SSL_CERT_FILE if SSL_CERT_FILE else None,
        ssl_ca_certs=None,
        ssl_cert_reqs=ssl.CERT_NONE,
    )
