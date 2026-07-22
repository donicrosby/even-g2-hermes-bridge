"""Structured logging setup for the even-g2 plugin.

Wraps stdlib ``logging`` with a JSON formatter so log output is machine-parseable
without adding any third-party runtime dependency. The Hermes Gateway loads
plugins by source path into its existing Python env — it does NOT resolve
``pyproject.toml`` deps — so any new runtime dep would require manual install
on the gateway host. Staying on stdlib only avoids that footgun entirely.

All plugin modules should call ``get_logger(__name__)`` instead of using
stdlib ``logging.getLogger`` directly. The returned ``StructuredLogger``
accepts kwargs that become structured fields in the output:

    LOG = get_logger(__name__)
    LOG.info("frame", direction="in", frame_type="hello", chat_id="g2-1")

Emits one JSON object per log line: ``{"event": "frame", "direction": "in",
"frame_type": "hello", "chat_id": "g2-1", "level": "info", "logger": "...",
"timestamp": "..."}``.

Env-var override: ``EVEN_G2_LOG_LEVEL=DEBUG`` (or WARNING, ERROR, etc).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

_FIELDS_ATTR = "_structured_fields"


class JsonFormatter(logging.Formatter):
    """Emit each log record as a single line of JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Render a log record as a compact JSON string."""
        payload: dict[str, Any] = {
            "level": record.levelname.lower(),
            "logger": record.name,
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
        }
        message = record.getMessage()
        if message:
            payload["event"] = message
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        fields = getattr(record, _FIELDS_ATTR, None)
        if isinstance(fields, dict):
            for key, value in fields.items():
                if key not in payload:
                    payload[key] = value
        return json.dumps(payload, default=str, separators=(",", ":"))


class StructuredLogger:
    """Wrapper around a stdlib logger that accepts kwargs as structured fields.

    Mirrors the structlog API surface we originally used
    (LOG.info(event, **fields), LOG.warning(...), LOG.error(...),
    LOG.exception(...), LOG.debug(...)) so call sites don't need to know
    whether structlog is backing it.

    Also accepts stdlib-style printf format args (LOG.info("msg %s", val))
    for backward compat with the rest of the codebase that hasn't been
    migrated yet. The formatted string becomes the ``event`` field in the
    JSON output.
    """

    def __init__(self, logger: logging.Logger) -> None:
        """Wrap a stdlib logger."""
        self._logger = logger

    def _log(
        self, level: int, event: str, *args: Any, **fields: Any,
    ) -> None:
        if not self._logger.isEnabledFor(level):
            return
        if args:
            event = event % args
        if fields:
            event = event + " " + " ".join(
                f"{k}={v}" for k, v in fields.items()
            )
        self._logger.log(level, event)

    def debug(self, event: str, *args: Any, **fields: Any) -> None:
        """Log at DEBUG level."""
        self._log(logging.DEBUG, event, *args, **fields)

    def info(self, event: str, *args: Any, **fields: Any) -> None:
        """Log at INFO level."""
        self._log(logging.INFO, event, *args, **fields)

    def warning(self, event: str, *args: Any, **fields: Any) -> None:
        """Log at WARNING level."""
        self._log(logging.WARNING, event, *args, **fields)

    def warn(self, event: str, *args: Any, **fields: Any) -> None:
        """Alias for warning()."""
        self.warning(event, *args, **fields)

    def error(self, event: str, *args: Any, **fields: Any) -> None:
        """Log at ERROR level."""
        self._log(logging.ERROR, event, *args, **fields)

    def exception(self, event: str, *args: Any, **fields: Any) -> None:
        """Log at ERROR level with exception traceback attached."""
        if not self._logger.isEnabledFor(logging.ERROR):
            return
        if args:
            event = event % args
        extra = {_FIELDS_ATTR: fields} if fields else {}
        self._logger.exception(event, extra=extra)


_CONFIGURED = False


def configure() -> None:
    """Attach the JSON formatter to the byoa_plugin logger once at import time.

    Idempotent — subsequent calls are no-ops. Safe to call from multiple modules.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    level_name = os.environ.get("EVEN_G2_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(JsonFormatter())
    plugin_logger = logging.getLogger("byoa_plugin")
    plugin_logger.handlers.clear()
    plugin_logger.addHandler(handler)
    plugin_logger.setLevel(level)

    _CONFIGURED = True


def get_logger(name: str) -> StructuredLogger:
    """Return a structured logger bound to ``name``.

    The first call configures the JSON handler; subsequent calls just wrap the
    named stdlib logger.
    """
    configure()
    return StructuredLogger(logging.getLogger(name))
