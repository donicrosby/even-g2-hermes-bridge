"""Hermes CLI commands for the even_g2 plugin.

Registers `hermes even-g2` with subcommands:
  - `hermes even-g2 setup` — generate token + (optional) Tailscale Serve
  - `hermes even-g2 qr`     — print QR code + write PNG
  - `hermes even-g2 url`    — print the advertised WSS URL only

CLI registration is wrapped in try/except in __init__.register() so a
broken CLI doesn't break plugin load.
"""

from __future__ import annotations

import logging
import sys

LOG = logging.getLogger("byoa_plugin.cli")


def register_cli(ctx: object) -> None:
    """Register `hermes even-g2` subcommands."""
    # Each subcommand is a function decorated or registered with ctx.
    # The exact API depends on the Hermes CLI framework; we use the most
    # generic form: register_command(name, handler).

    def _setup(args: list[str]) -> int:
        from byoa_plugin.config import BridgeConfig
        from byoa_plugin.setup_flow import setup as run_setup

        cfg = BridgeConfig.from_env()
        force = "--force-token" in args
        status = run_setup(cfg, force_token=force)
        print()
        print("  even_g2 setup complete:")
        print(f"    bind:        {status['bind']}")
        print(f"    net_mode:    {status['net_mode']}")
        print(f"    public_url:  {status['public_url'] or '(not set)'}")
        print(f"    token:       {status['token'][:8]}... (set in EVEN_G2_BRIDGE_TOKEN)")
        print(f"    tailscale:   {'available' if status['tailscale_available'] else 'not available'}")
        if not status["public_url"] and status["net_mode"] != "lan":
            print()
            print("  ⚠ No public URL configured. Either:")
            print("    1. Install + configure Tailscale (recommended)")
            print("    2. Set EVEN_G2_BRIDGE_PUBLIC_URL=wss://your-external-url")
            print("       and configure your reverse proxy to forward to "
                  f"http://127.0.0.1:{cfg.ws_port}")
        print()
        return 0

    def _qr(_args: list[str]) -> int:
        from byoa_plugin.config import BridgeConfig
        from byoa_plugin.qr_setup import print_qr

        cfg = BridgeConfig.from_env()
        if not cfg.token:
            print("ERROR: EVEN_G2_BRIDGE_TOKEN is not set. Run `hermes even-g2 setup` first.", file=sys.stderr)
            return 1
        print_qr(cfg)
        return 0

    def _url(_args: list[str]) -> int:
        from byoa_plugin.config import BridgeConfig

        cfg = BridgeConfig.from_env()
        print(cfg.advertised_url)
        return 0

    ctx.register_cli_command(
        name="even-g2 setup",
        handler=_setup,
        help_text="Generate bridge token + configure network exposure",
    )
    ctx.register_cli_command(
        name="even-g2 qr",
        handler=_qr,
        help_text="Print QR code for glasses-app bootstrap",
    )
    ctx.register_cli_command(
        name="even-g2 url",
        handler=_url,
        help_text="Print the advertised WSS URL",
    )
