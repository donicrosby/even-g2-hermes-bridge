"""Structured logging setup for the even-g2 plugin.

Wraps ``structlog`` with a project-standard JSON renderer and an env-var-driven
level override (``EVEN_G2_LOG_LEVEL=DEBUG``).

All plugin modules should call ``get_logger(__name__)`` instead of using
stdlib ``logging.getLogger`` directly. The returned ``structlog.BoundLogger``
accepts kwargs that become structured fields in the output:

    LOG = get_logger(__name__)
    LOG.info("frame", direction="in", frame_type="hello", chat_id="g2-1")

Emits one JSON object per log line: ``{"event": "frame", "direction": "in",
"frame_type": "hello", "chat_id": "g2-1", "level": "info", "logger": "...",
"timestamp": "..."}``.
"""

from __future__ import annotations

import logging
import os
import sys

import structlog


def _resolved_level() -> int:
    """Resolve the log level from ``EVEN_G2_LOG_LEVEL`` (default INFO)."""
    return getattr(
        logging,
        os.environ.get("EVEN_G2_LOG_LEVEL", "INFO").upper(),
        logging.INFO,
    )


def configure() -> None:
    """Configure structlog + stdlib logging once at plugin import time.

    Idempotent — safe to call multiple times (subsequent calls are no-ops).
    """
    level = _resolved_level()

    # Stdlib logging bridges to structlog's processors; configure once.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=level,
        force=False,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.WriteLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structured logger bound to ``name``.

    The first call configures structlog globally; subsequent calls just create
    new bound loggers.
    """
    configure()
    return structlog.get_logger(name)
