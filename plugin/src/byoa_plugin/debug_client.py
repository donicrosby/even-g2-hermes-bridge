"""Debug CLI client for the even-g2 bridge WebSocket protocol.

Connects to a bridge server, sends a hello frame, and logs every frame
received. Useful for reproducing connection issues without the glasses-app.

Examples:
    uv run python -m byoa_plugin.debug_client --url ws://127.0.0.1:8767 \\
        --token $TOKEN

    uv run python -m byoa_plugin.debug_client --url ... --token ... \\
        --send text:"hello world" \\
        --send sessions.list \\
        --timeout 15 --debug
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from collections import Counter
from typing import TYPE_CHECKING

import anyio
from google.protobuf.json_format import MessageToDict
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from byoa_plugin import wire
from byoa_plugin.proto_gen import hermes_bridge_pb2 as _pb

if TYPE_CHECKING:
    from collections.abc import Sequence

    from websockets.asyncio.client import ClientConnection

LOG = logging.getLogger("debug_client")


def parse_send_spec(spec: str) -> tuple[bytes, str]:
    """Parse a --send spec of the form ``<frame_type>:<arg>`` into serialized bytes.

    Recognized frame types match the inbound wire.* constructors:
    ``text``, ``sessions.list``, ``sessions.switch:<target>``,
    ``sessions.new``, ``audio.start``, ``audio.stop``, ``stop``.

    Returns a tuple of (serialized_bytes, frame_type_label).
    Raises ValueError on unknown frame types or missing required args.
    """
    head, _, rest = spec.partition(":")
    head = head.strip().lower()
    rest = rest.strip()

    if head == "text":
        if not rest:
            raise ValueError("text: requires a content argument")
        return wire.text(rest), "text"
    if head in {"sessions.list", "sessions_list"}:
        return wire.sessions_list(), "sessions.list"
    if head in {"sessions.switch", "sessions_switch"}:
        if not rest:
            raise ValueError("sessions.switch: requires a target argument")
        return wire.sessions_switch(rest), "sessions.switch"
    if head in {"sessions.new", "sessions_new"}:
        return wire.sessions_new(), "sessions.new"
    if head in {"audio.start", "audio_start"}:
        return wire.audio_start(), "audio.start"
    if head in {"audio.stop", "audio_stop"}:
        return wire.audio_stop(), "audio.stop"
    if head == "stop":
        return wire.stop(), "stop"
    raise ValueError(f"unknown frame type in --send spec: {head!r}")


def format_summary(
    sent: Counter[str],
    received: Counter[str],
) -> str:
    """Format the final frame-count summary table."""
    all_kinds = sorted(set(sent) | set(received))
    if not all_kinds:
        return "(no frames exchanged)"
    width = max(len(k) for k in all_kinds)
    header = f"  {'frame_type':<{width}}  sent  received"
    sep = f"  {'-' * width}  ----  --------"
    rows = [f"  {k:<{width}}  {sent.get(k, 0):>4}  {received.get(k, 0):>8}"
            for k in all_kinds]
    return "\n".join([header, sep, *rows])


async def _receive_loop(
    ws: ClientConnection,
    timeout: float,
    debug: bool,
    received: Counter[str],
) -> None:
    """Pull frames from ``ws`` until close, timeout, or signal-driven shutdown."""
    shutdown = anyio.Event()

    def handler(signum: int, _frame: object) -> None:
        LOG.info("received_signal", extra={"signum": signum})
        shutdown.set()

    old_int = signal.signal(signal.SIGINT, handler)
    old_term = signal.signal(signal.SIGTERM, handler)
    try:
        with anyio.move_on_after(timeout):
            while not shutdown.is_set():
                try:
                    raw = await ws.recv()
                except ConnectionClosed:
                    break
                if not isinstance(raw, (bytes, bytearray)):
                    LOG.warning("non_binary_message",
                                extra={"type": type(raw).__name__})
                    continue
                try:
                    frame = wire.parse_frame(bytes(raw))
                except wire.FrameParseError as e:
                    LOG.warning("frame_decode_error",
                                extra={"error": str(e), "byte_size": len(raw)})
                    continue
                kind = frame.WhichOneof("payload")
                if kind is None:
                    LOG.warning("empty_frame", extra={"byte_size": len(raw)})
                    continue
                received[kind] += 1
                LOG.info("frame", extra=_frame_fields(frame, debug))
    finally:
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)


async def run_client(
    url: str,
    token: str,
    send_specs: Sequence[str],
    timeout: float,
    debug: bool,
) -> int:
    """Connect, send hello + each --send frame, log inbound, print summary.

    Returns the exit code (0 on clean shutdown, 1 on connect/handshake error).
    """
    sent = Counter[str]()
    received = Counter[str]()

    try:
        async with connect(url, max_size=2**20) as ws:
            LOG.info("ws_open", extra={"url": url})
            hello_bytes = wire.hello(token, "debug-client")
            await ws.send(hello_bytes)
            sent["hello"] += 1
            LOG.info("frame", extra={"direction": "out", "frame_type": "hello",
                                     "byte_size": len(hello_bytes)})

            with anyio.fail_after(timeout):
                raw_first = await ws.recv()
            if not isinstance(raw_first, (bytes, bytearray)):
                LOG.error("handshake_non_binary",
                          extra={"type": type(raw_first).__name__})
                return 1
            first_frame = wire.parse_frame(bytes(raw_first))
            first_kind = first_frame.WhichOneof("payload")
            if first_kind != "hello_ok":
                LOG.error("handshake_failed",
                          extra={"expected": "hello_ok", "got": first_kind})
                return 1
            received["hello.ok"] += 1
            LOG.info("hello_ok_received", extra=_frame_fields(first_frame, debug))

            for spec in send_specs:
                bytes_to_send, label = parse_send_spec(spec)
                await ws.send(bytes_to_send)
                sent[label] += 1
                LOG.info("frame",
                         extra={"direction": "out", "frame_type": label,
                                "byte_size": len(bytes_to_send)})

            await _receive_loop(ws, timeout, debug, received)

            await ws.close()
            LOG.info("ws_close")
    except TimeoutError:
        LOG.warning("connect_timeout", extra={"url": url, "timeout_s": timeout})
        return 1
    except OSError as e:
        LOG.error("connect_failed", extra={"url": url, "error": str(e)})
        return 1

    sys.stdout.write("\n" + format_summary(sent, received) + "\n")
    return 0


def _frame_fields(frame: _pb.Frame, debug: bool) -> dict[str, object]:
    """Extract loggable fields from a Frame. Full payload at DEBUG level."""
    kind = frame.WhichOneof("payload")
    fields: dict[str, object] = {
        "direction": "in",
        "frame_type": kind,
    }
    if debug and kind is not None:
        fields["payload"] = MessageToDict(getattr(frame, kind))
    return fields


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="byoa_plugin.debug_client",
        description="Debug CLI for the even-g2 bridge WebSocket protocol.",
    )
    parser.add_argument("--url", required=True,
                        help="Bridge WS URL (e.g. ws://127.0.0.1:8767)")
    parser.add_argument("--token", required=True,
                        help="Bridge auth token (BYOA_TOKEN)")
    parser.add_argument("--send", action="append", default=[],
                        metavar="TYPE[:ARG]",
                        help="Frame to send after hello. Can be repeated. "
                             "Recognized: text:<content>, sessions.list, "
                             "sessions.switch:<target>, sessions.new, "
                             "audio.start, audio.stop, stop")
    parser.add_argument("--timeout", type=float, default=30.0,
                        help="Timeout in seconds for connect/handshake/idle "
                             "(default: 30)")
    parser.add_argument("--debug", action="store_true",
                        help="Log full decoded payload of each frame")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

    return anyio.run(run_client, args.url, args.token, args.send,
                     args.timeout, args.debug)


if __name__ == "__main__":
    sys.exit(main())
