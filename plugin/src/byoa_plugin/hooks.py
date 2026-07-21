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
    """Register pre/post tool-call hooks with the gateway.

    Called from plugin register(ctx). Stores the adapter reference for the
    hook callbacks to use.
    """
    # We can't access the adapter instance yet (it's constructed later by
    # the gateway via adapter_factory). We register the hooks now; they'll
    # look up the adapter lazily via _get_adapter().
    try:
        ctx.register_hook("pre_tool_call", _pre_tool_call)
        ctx.register_hook("post_tool_call", _post_tool_call)
        LOG.debug("tool-call hooks registered")
    except (AttributeError, TypeError) as e:
        LOG.warning("failed to register tool-call hooks: %s", e)


def set_adapter(adapter: EvenG2Adapter) -> None:
    """Called by the adapter when it finishes connect()."""
    global _ADAPTER
    _ADAPTER = adapter


def _get_adapter() -> EvenG2Adapter | None:
    return _ADAPTER


# ---- Salient argument extraction for tool labels --------------------------


_SALIENT_ARG_KEYS = ("command", "query", "path", "url", "file", "code", "name", "input")


def tool_label(tool_name: str, args: object) -> str:
    """Produce a compact human-readable label for a tool invocation.

    Example: tool_label("web_search", {"query": "weather today"})
             → "web_search: weather today"

    Truncates to MAX_LABEL_LEN chars; falls back to the bare tool name if
    no salient argument is found.
    """
    MAX_LABEL_LEN = 64
    if isinstance(args, dict):
        for key in _SALIENT_ARG_KEYS:
            if args.get(key):
                val = str(args[key]).strip()
                if not val:
                    continue
                val = val.replace("\n", " ").strip()
                prefix = f"{tool_name}: "
                budget = MAX_LABEL_LEN - len(prefix)
                if budget <= 4:
                    return tool_name[:MAX_LABEL_LEN]
                if len(val) > budget:
                    val = val[: budget - 3] + "..."
                return prefix + val
    return tool_name[:MAX_LABEL_LEN]


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
