"""Setup flow for the even-g2 plugin.

`hermes even-g2 setup`:
  1. Generates EVEN_G2_BRIDGE_TOKEN if missing
  2. Binds the WS server to loopback (Tailscale pattern) or 0.0.0.0
     (reverse-proxy pattern, configurable via EVEN_G2_BRIDGE_NET)
  3. If Tailscale is detected, runs `tailscale serve` to expose the local
     WS as a private wss:// endpoint
  4. If Tailscale is NOT detected, prints clear instructions for setting
     up a reverse proxy and configuring EVEN_G2_BRIDGE_PUBLIC_URL
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from byoa_plugin.net import tailscale_available, tailscale_status

if TYPE_CHECKING:
    from byoa_plugin.config import BridgeConfig


class TailscaleNotFound(RuntimeError):
    """`tailscale` binary is not on PATH."""


LOG = logging.getLogger("byoa_plugin.setup_flow")

HERMES_HOME = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes")))


def _generate_token() -> str:
    """Return a 32-char URL-safe random token."""
    return secrets.token_urlsafe(24)


def _write_env_file(key: str, value: str) -> None:
    """Persist a key=value pair to ~/.hermes/.env (best-effort)."""
    HERMES_HOME.mkdir(parents=True, exist_ok=True)
    env_file = HERMES_HOME / ".env"
    lines: list[str] = []
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if not line.startswith(f"{key}=") and line.strip():
                lines.append(line)
    lines.append(f"{key}={value}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    LOG.info("wrote %s to %s", key, env_file)


def ensure_token(*, force: bool = False) -> str:
    """Read or generate EVEN_G2_BRIDGE_TOKEN. Persists to ~/.hermes/.env."""
    existing = os.getenv("EVEN_G2_BRIDGE_TOKEN", "").strip()
    if existing and not force:
        return existing
    token = _generate_token()
    os.environ["EVEN_G2_BRIDGE_TOKEN"] = token
    try:
        _write_env_file("EVEN_G2_BRIDGE_TOKEN", token)
    except OSError as e:
        LOG.warning("could not persist token: %s", e)
    return token


def build_serve_command(cfg: BridgeConfig, serve_port: int | None = None) -> list[str]:
    """Build the `tailscale serve` argv. Caller runs it via subprocess."""
    binary = shutil.which("tailscale")
    if binary is None:
        raise TailscaleNotFound
    https_port = serve_port if serve_port is not None else cfg.tailscale_serve_port
    target = f"http://127.0.0.1:{cfg.ws_port}"
    return [binary, "serve", f"--https={https_port}", "--bg", target]


def enable_tailscale_serve(cfg: BridgeConfig) -> str | None:
    """Run `tailscale serve`. Returns the resulting WSS URL or None on failure."""
    try:
        argv = build_serve_command(cfg)
        LOG.info("running: %s", " ".join(argv))
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=15.0)
    except (subprocess.TimeoutExpired, OSError, RuntimeError) as e:
        LOG.warning("tailscale serve failed: %s", e)
        return None
    if proc.returncode != 0:
        LOG.warning(
            "tailscale serve rc=%d stderr=%s", proc.returncode, proc.stderr[:200],
        )
        return None

    # Resolve the WSS URL from Tailscale status.
    status = tailscale_status()
    if status is None:
        return None
    self_info = status.get("Self") or {}
    dns_name = self_info.get("DNSName", "").rstrip(".")
    magic_suffix = status.get("MagicDNSSuffix", "")
    if dns_name:
        host = dns_name
    elif self_info.get("HostName") and magic_suffix:
        host = f"{self_info['HostName']}.{magic_suffix}"
    else:
        return None
    return f"wss://{host}:{cfg.tailscale_serve_port}"


def setup(cfg: BridgeConfig, *, force_token: bool = False) -> dict:
    """Top-level setup routine. Returns a status dict for CLI/dashboard use.

    Steps:
      1. Ensure token exists (generate if missing, persist to ~/.hermes/.env)
      2. If cfg.net_mode == "tailscale": run `tailscale serve`, set public_url
      3. If cfg.net_mode == "reverse-proxy": print instructions
      4. If cfg.net_mode == "lan": bind directly to 0.0.0.0
    """
    token = ensure_token(force=force_token)
    cfg.token = token

    public_url = cfg.explicit_public_url

    if cfg.net_mode == "tailscale":
        if not tailscale_available():
            LOG.warning(
                "EVEN_G2_BRIDGE_NET=tailscale but tailscale binary not found; "
                "falling back to reverse-proxy instructions",
            )
            public_url = public_url or None
        else:
            url = enable_tailscale_serve(cfg)
            if url:
                public_url = url
                os.environ["EVEN_G2_BRIDGE_PUBLIC_URL"] = url
                try:
                    _write_env_file("EVEN_G2_BRIDGE_PUBLIC_URL", url)
                except OSError:
                    pass
            else:
                LOG.warning("tailscale serve failed; user must set up manually")

    if public_url:
        cfg.public_url = public_url
        cfg.explicit_public_url = public_url

    return {
        "token": token,
        "net_mode": cfg.net_mode,
        "public_url": public_url,
        "tailscale_available": tailscale_available(),
        "bind": f"{cfg.ws_host}:{cfg.ws_port}",
    }
