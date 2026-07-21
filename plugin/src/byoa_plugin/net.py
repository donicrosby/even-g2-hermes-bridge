"""Network exposure helpers for the even_g2 plugin.

Resolves the externally-advertised URL the glasses-app will connect to.
Priority: explicit EVEN_G2_BRIDGE_PUBLIC_URL → Tailscale MagicDNS → LAN IP.
"""

from __future__ import annotations

import json
import logging
import shutil
import socket
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from byoa_plugin.config import BridgeConfig

LOG = logging.getLogger("byoa_plugin.net")


def tailscale_status() -> dict | None:
    """Return parsed `tailscale status --json` or None if Tailscale isn't available."""

    binary = shutil.which("tailscale")
    if binary is None:
        return None
    try:
        proc = subprocess.run(
            [binary, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        LOG.debug("tailscale status failed: %s", e)
        return None
    if proc.returncode != 0:
        LOG.debug("tailscale status rc=%d", proc.returncode)
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        LOG.debug("tailscale status json parse failed: %s", e)
        return None


def tailscale_available() -> bool:
    return tailscale_status() is not None


def _lan_ip() -> str | None:
    """Best-effort LAN IPv4 of this host. None if undeterminable."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Doesn't actually connect — just picks an interface IP for routing.
            s.connect(("8.8.8.8", 53))
            ip = s.getsockname()[0]
        finally:
            s.close()
        return ip if ip and not ip.startswith("127.") else None
    except OSError:
        return None


def resolve_advertised_url(cfg: BridgeConfig) -> str:
    """Compute the WSS URL the glasses-app should connect to.

    Priority (highest first):
      1. cfg.explicit_public_url (from EVEN_G2_BRIDGE_PUBLIC_URL)
      2. Tailscale MagicDNS URL via `tailscale status --json`
      3. LAN IP fallback with explicit ws:// scheme (no TLS — dev only)
    """
    # 1. Explicit override always wins.
    if cfg.explicit_public_url:
        return cfg.explicit_public_url.rstrip("/")

    # 2. Tailscale MagicDNS.
    status = tailscale_status()
    if status is not None:
        magic_dns = status.get("MagicDNSSuffix") or status.get("CurrentTailnet", {}).get("MagicDNSSuffix")
        self_info = status.get("Self") or {}
        host_name = self_info.get("HostName") or self_info.get("DNSName")
        if host_name and "." not in host_name and magic_dns:
            host = f"{host_name}.{magic_dns}"
        elif host_name:
            host = host_name.rstrip(".")
        else:
            host = None

        if host:
            # Strip protocol if user accidentally included it.
            if host.startswith("https://"):
                host = host[len("https://"):]
            elif host.startswith("http://"):
                host = host[len("http://"):]
            return f"wss://{host}:{cfg.tailscale_serve_port}"

    # 3. LAN fallback.
    ip = _lan_ip()
    if ip:
        LOG.warning(
            "no public URL configured; advertising LAN ws://%s:%d "
            "(plaintext — use a reverse proxy or Tailscale for production)",
            ip,
            cfg.ws_port,
        )
        return f"ws://{ip}:{cfg.ws_port}"

    return f"ws://127.0.0.1:{cfg.ws_port}"
