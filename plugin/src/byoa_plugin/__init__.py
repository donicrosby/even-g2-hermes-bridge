"""Hermes platform plugin for Even Realities G2 smart glasses.

Bridges the glasses-app (custom Even Hub SDK app) to the Hermes Gateway via a
persistent WebSocket connection. Streaming tokens, tool-call activity, session
switching, and voice ASR are all handled through this single WS protocol.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from byoa_plugin.config import BridgeConfig


PLUGIN_NAME = "even_g2"
PLUGIN_LABEL = "Even Realities G2"
PLUGIN_EMOJI = "👓"
PLUGIN_HINT = (
    "You are talking to the user through Even Realities G2 smart glasses "
    "with a 576x288 monochrome micro-LED display. Keep replies concise "
    "and easy to scan. Markdown is supported but renders as plain text."
)


def _env_enablement() -> dict | None:
    """Seed PlatformConfig.extra from env vars for env-only setups.

    Called by Hermes before adapter construction so that env-driven
    configurations show up in `hermes gateway status` without SDK
    instantiation.
    """
    token = os.getenv("EVEN_G2_BRIDGE_TOKEN", "").strip()
    if not token:
        return None
    seed: dict[str, object] = {"token": token}
    host = os.getenv("EVEN_G2_BRIDGE_HOST", "").strip()
    if host:
        seed["host"] = host
    port = os.getenv("EVEN_G2_BRIDGE_PORT", "").strip()
    if port:
        seed["port"] = int(port)
    public_url = os.getenv("EVEN_G2_BRIDGE_PUBLIC_URL", "").strip()
    if public_url:
        seed["public_url"] = public_url
    return seed


def check_fn() -> bool:
    """Return True when the plugin's required env vars are present."""
    return bool(os.getenv("EVEN_G2_BRIDGE_TOKEN", "").strip())


def adapter_factory(cfg: BridgeConfig):  # type: ignore[name-defined]
    """Construct the EvenG2Adapter. Imported lazily so that the plugin module
    can be loaded (for env_enablement, check_fn) without pulling the full
    websockets/faster-whisper dep tree on every gateway status call.
    """
    from byoa_plugin.adapter import EvenG2Adapter

    return EvenG2Adapter(cfg)


def register(ctx: object) -> None:
    """Plugin entry point — called by the Hermes plugin system."""
    from byoa_plugin.hooks import bind as bind_hooks

    ctx.register_platform(
        name=PLUGIN_NAME,
        label=PLUGIN_LABEL,
        adapter_factory=adapter_factory,
        check_fn=check_fn,
        required_env=["EVEN_G2_BRIDGE_TOKEN"],
        env_enablement_fn=_env_enablement,
        cron_deliver_env_var="EVEN_G2_HOME_CHANNEL",
        allowed_users_env="EVEN_G2_ALLOWED_USERS",
        allow_all_env="EVEN_G2_ALLOW_ALL_USERS",
        max_message_length=2000,
        emoji=PLUGIN_EMOJI,
        platform_hint=PLUGIN_HINT,
    )

    # Register CLI commands (deferred import — pulls qr/setup deps only when invoked)
    try:
        from byoa_plugin.cli import register_cli

        register_cli(ctx)
    except ImportError as e:
        # Optional commands; missing deps shouldn't break plugin registration.
        import logging

        logging.getLogger("byoa_plugin").warning(
            "even_g2 CLI commands unavailable: %s", e,
        )

    # Bind tool-call hooks
    bind_hooks(ctx)
