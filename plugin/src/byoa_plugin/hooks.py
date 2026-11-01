"""Tool-call hooks for the even-g2 plugin.

Binds pre_tool_call and post_tool_call Hermes Gateway hooks so the
glasses-app sees `tool.start` / `tool.end` frames while the agent is
running tools (e.g. "🔍 Searching the web...").
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from byoa_plugin import protocol as proto

if TYPE_CHECKING:
    from byoa_plugin.adapter import EvenG2Adapter

LOG = logging.getLogger("byoa_plugin.hooks")

# Module-level reference to the active adapter. Set by bind() at plugin
# registration time. The gateway calls our hook functions with kwargs only,
# so we can't pass the adapter through the hook signature — has to be a
# module global.
_ADAPTER: EvenG2Adapter | None = None


def bind(ctx: object) -> None:
    """Register pre/post tool-call and session lifecycle hooks with the gateway.

    Called from plugin register(ctx). Stores the adapter reference for the
    hook callbacks to use.
    """
    # We can't access the adapter instance yet (it's constructed later by
    # the gateway via adapter_factory). We register the hooks now; they'll
    # look up the adapter lazily via _get_adapter().
    try:
        ctx.register_hook("pre_tool_call", _pre_tool_call)
        ctx.register_hook("post_tool_call", _post_tool_call)
        ctx.register_hook("on_session_start", _on_session_start)
        ctx.register_hook("on_session_reset", _on_session_reset)
        ctx.register_hook("on_session_end", _on_session_end)
        ctx.register_hook("on_session_finalize", _on_session_finalize)
        LOG.debug(
            "hooks registered: pre_tool_call, post_tool_call, "
            "on_session_start, on_session_reset, on_session_end, "
            "on_session_finalize",
        )
    except (AttributeError, TypeError) as e:
        LOG.warning("failed to register hooks: %s", e)


def set_adapter(adapter: EvenG2Adapter) -> None:
    """Called by the adapter when it finishes connect()."""
    global _ADAPTER  # module-level adapter ref  # noqa: PLW0603
    _ADAPTER = adapter


def _get_adapter() -> EvenG2Adapter | None:
    return _ADAPTER


# ---- Salient argument extraction for tool labels --------------------------


_SALIENT_ARG_KEYS = ("command", "query", "path", "url", "file", "code", "name", "input")
_LABEL_TRUNCATION_THRESHOLD = 4


def tool_label(tool_name: str, args: object) -> str:
    """Produce a compact human-readable label for a tool invocation.

    Example: tool_label("web_search", {"query": "weather today"})
             → "web_search: weather today"

    Truncates to MAX_LABEL_LEN chars; falls back to the bare tool name if
    no salient argument is found.
    """
    max_label_len = 64
    if isinstance(args, dict):
        for key in _SALIENT_ARG_KEYS:
            if args.get(key):
                val = str(args[key]).strip()
                if not val:
                    continue
                val = val.replace("\n", " ").strip()
                prefix = f"{tool_name}: "
                budget = max_label_len - len(prefix)
                if budget <= _LABEL_TRUNCATION_THRESHOLD:
                    return tool_name[:max_label_len]
                if len(val) > budget:
                    val = val[: budget - 3] + "..."
                return prefix + val
    return tool_name[:max_label_len]


# ---- Hook implementations ------------------------------------------------


def _pre_tool_call(
    *,
    tool_name: str,
    args: object = None,
    _task_id: object = None,
    session_id: object = None,
    _tool_call_id: object = None,
    **_: object,
) -> None:
    """Called by the gateway before a tool runs. Emits `tool.start` frame."""
    adapter = _get_adapter()
    if adapter is None:
        return

    chat_id = adapter.chat_for_session(str(session_id)) if session_id else None
    if not chat_id:
        LOG.debug(
            "pre_tool_call: no chat_id for session_id=%s tool=%s",
            session_id,
            tool_name,
        )
        return

    label = tool_label(tool_name, args)
    LOG.info("tool.start chat_id=%s tool=%s label=%s", chat_id, tool_name, label)
    frame = proto.tool_start(tool_name, label=label)
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return
    if loop.is_running():
        asyncio.run_coroutine_threadsafe(
            adapter.registry.send_frame(chat_id, frame), loop,
        )
    else:
        task = loop.create_task(adapter.registry.send_frame(chat_id, frame))
        adapter._bg_tasks.add(task)  # noqa: SLF001
        task.add_done_callback(adapter._bg_tasks.discard)  # noqa: SLF001


def _post_tool_call(
    *,
    tool_name: str,
    _args: object = None,
    _result: object = None,
    _task_id: object = None,
    session_id: object = None,
    _tool_call_id: object = None,
    error: object = None,
    **_: object,
) -> None:
    """Called by the gateway after a tool completes. Emits `tool.end`."""
    adapter = _get_adapter()
    if adapter is None:
        return

    chat_id = adapter.chat_for_session(str(session_id)) if session_id else None
    if not chat_id:
        return

    ok = error is None
    LOG.info("tool.end chat_id=%s tool=%s ok=%s", chat_id, tool_name, ok)
    frame = proto.tool_end(tool_name, ok=ok)
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return
    if loop.is_running():
        asyncio.run_coroutine_threadsafe(
            adapter.registry.send_frame(chat_id, frame), loop,
        )
    else:
        task = loop.create_task(adapter.registry.send_frame(chat_id, frame))
        adapter._bg_tasks.add(task)  # noqa: SLF001
        task.add_done_callback(adapter._bg_tasks.discard)  # noqa: SLF001


# ---- Session lifecycle hooks ----------------------------------------------


def _resolve_chat_id(adapter: EvenG2Adapter) -> str | None:
    """Return the most recent inbound chat_id, or None if no frame has arrived.

    Plugin session hooks don't receive chat_id in their payload (verified
    upstream contract); this pointer is the only way to attribute a session
    event to a glasses pair. v1 limitation: single-pair per adapter.
    """
    chat_id = getattr(adapter, "_last_chat_id", None)
    if chat_id is None:
        LOG.debug("session hook: no last_chat_id; skipping")
    return chat_id


def _emit_active_frame(adapter: EvenG2Adapter, chat_id: str, session_id: str) -> None:
    """Send `proto.active(session_id, name=session_id[:16])` to chat_id."""
    frame = proto.active(session_id, name=session_id[:16])
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return
    if loop.is_running():
        asyncio.run_coroutine_threadsafe(
            adapter.registry.send_frame(chat_id, frame), loop,
        )
    else:
        task = loop.create_task(adapter.registry.send_frame(chat_id, frame))
        adapter._bg_tasks.add(task)  # noqa: SLF001
        task.add_done_callback(adapter._bg_tasks.discard)  # noqa: SLF001


def _record_and_emit(session_id: str) -> None:
    """Shared body for _on_session_start and _on_session_reset.

    Records the session_id → chat_id binding (so chat_for_session keeps
    working for tool-call hooks) and emits an `active` frame to the glasses.
    """
    adapter = _get_adapter()
    if adapter is None:
        return
    chat_id = _resolve_chat_id(adapter)
    if chat_id is None:
        return
    adapter._session_by_chat[chat_id] = session_id  # noqa: SLF001
    LOG.info(
        "session bound chat_id=%s session_id=%s — emitting active frame",
        chat_id,
        session_id,
    )
    _emit_active_frame(adapter, chat_id, session_id)


def _on_session_start(
    *,
    session_id: str,
    _model: object = None,
    _platform: object = None,
    **_: object,
) -> None:
    """Gateway created a new session. Bind it and emit `active` to glasses."""
    _record_and_emit(session_id)


def _on_session_reset(
    *,
    session_id: str,
    _platform: object = None,
    **_: object,
) -> None:
    """Gateway swapped in a fresh session key (e.g. after /new). Same as start."""
    _record_and_emit(session_id)


def _on_session_end(
    *,
    session_id: str,
    completed: object = None,
    interrupted: object = None,
    _model: object = None,
    _platform: object = None,
    **_: object,
) -> None:
    """End of a run_conversation() call. Log only — no UI action needed."""
    LOG.info(
        "session end sid=%s completed=%s interrupted=%s",
        session_id,
        completed,
        interrupted,
    )


def _on_session_finalize(
    *,
    session_id: str | None = None,
    _platform: object = None,
    **_: object,
) -> None:
    """Gateway tears down a session. Remove the reverse mapping if present."""
    if session_id is None:
        return
    adapter = _get_adapter()
    if adapter is None:
        return
    # Find any chat_id whose bound session_id matches and remove it.
    # v1: at most one match expected.
    matching = [
        cid
        for cid, sid in adapter._session_by_chat.items()  # noqa: SLF001
        if sid == session_id
    ]
    for cid in matching:
        del adapter._session_by_chat[cid]  # noqa: SLF001
        LOG.debug("session finalize: removed chat_id=%s -> session_id=%s", cid, session_id)
