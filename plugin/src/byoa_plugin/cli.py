"""Hermes CLI commands for the even-g2 plugin.

Registers `hermes even-g2` with subcommands:
  - `hermes even-g2 setup` — generate token + (optional) Tailscale Serve
  - `hermes even-g2 qr`     — print QR code + write PNG
  - `hermes even-g2 url`    — print the advertised WSS URL only

Uses the Hermes register_cli_command API:
  - name: the top-level command ("even-g2")
  - help: short description shown in hermes --help
  - setup_fn: builds the argparse subcommand tree
  - handler_fn: dispatches based on the parsed subcommand
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse

LOG = logging.getLogger("byoa_plugin.cli")


def _setup_argparse(subparser: argparse.ArgumentParser) -> None:
    """Build the argparse tree for `hermes even-g2 <subcommand>`."""
    subs = subparser.add_subparsers(dest="even-g2_command")

    setup_p = subs.add_parser("setup", help="Generate bridge token + configure network")
    setup_p.add_argument(
        "--force-token",
        action="store_true",
        help="Force-regenerate the bridge token",
    )

    subs.add_parser("qr", help="Print QR code for glasses-app bootstrap")
    subs.add_parser("url", help="Print the advertised WSS URL")


def _handle_command(args: argparse.Namespace) -> int:
    """Dispatch based on the parsed subcommand."""
    cmd = getattr(args, "even-g2_command", None)

    if cmd == "setup":
        return _do_setup(args)
    if cmd == "qr":
        return _do_qr()
    if cmd == "url":
        return _do_url()

    print("Usage: hermes even-g2 <setup|qr|url>", file=sys.stderr)
    return 1


def _do_setup(args: argparse.Namespace) -> int:
    from byoa_plugin.config import BridgeConfig
    from byoa_plugin.setup_flow import setup as run_setup

    cfg = BridgeConfig.from_env()
    force = getattr(args, "force_token", False)
    status = run_setup(cfg, force_token=force)
    print()
    print("  even-g2 setup complete:")
    print(f"    bind:        {status['bind']}")
    print(f"    net_mode:    {status['net_mode']}")
    print(f"    public_url:  {status['public_url'] or '(not set)'}")
    print(f"    token:       {status['token'][:8]}... (set in EVEN_G2_BRIDGE_TOKEN)")
    print(
        f"    tailscale:   "
        f"{'available' if status['tailscale_available'] else 'not available'}",
    )
    if not status["public_url"] and status["net_mode"] != "lan":
        print()
        print("  ⚠ No public URL configured. Either:")
        print("    1. Install + configure Tailscale (recommended)")
        print("    2. Set EVEN_G2_BRIDGE_PUBLIC_URL=wss://your-external-url")
        print(
            "       and configure your reverse proxy to forward to "
            f"http://127.0.0.1:{cfg.ws_port}",
        )
    print()
    return 0


def _do_qr() -> int:
    from byoa_plugin.config import BridgeConfig
    from byoa_plugin.qr_setup import print_qr

    cfg = BridgeConfig.from_env()
    if not cfg.token:
        print(
            "ERROR: EVEN_G2_BRIDGE_TOKEN is not set. "
            "Run `hermes even-g2 setup` first.",
            file=sys.stderr,
        )
        return 1
    print_qr(cfg)
    return 0


def _do_url() -> int:
    from byoa_plugin.config import BridgeConfig

    cfg = BridgeConfig.from_env()
    print(cfg.advertised_url)
    return 0


def register_cli(ctx: object) -> None:
    """Register `hermes even-g2` CLI subcommand tree."""
    ctx.register_cli_command(  # type: ignore[attr-defined]
        name="even-g2",
        help="Even G2 bridge management (setup, qr, url)",
        setup_fn=_setup_argparse,
        handler_fn=_handle_command,
    )
