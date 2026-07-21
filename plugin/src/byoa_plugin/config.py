"""Configuration for the even_g2 plugin.

All settings are env-var-driven for parity with Hermes's env-enablement model.
`BridgeConfig.from_env()` is the canonical factory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _hermes_home() -> Path:
    return Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes")))


@dataclass
class BridgeConfig:
    """Resolved configuration for the even_g2 plugin."""

    token: str = ""
    ws_host: str = "127.0.0.1"
    ws_port: int = 8767
    public_url: str = ""

    # Network exposure mode: "tailscale" (auto-serve), "reverse-proxy" (user
    # provides their own), or "lan" (direct bind, plaintext, dev only).
    net_mode: str = "tailscale"
    tailscale_serve_port: int = 8443

    # Voice ASR backends (tried in order: litellm → sidecar → whisper-cpu)
    asr_litellm_model: str = ""
    asr_litellm_base_url: str = ""
    asr_litellm_api_key: str = ""
    asr_sidecar_bin: str = ""
    asr_state_path: Path = field(default_factory=lambda: _hermes_home() / "even_g2_asr.json")

    # Allowed users (optional ACL — defaults to allow-all for single-user)
    allowed_users: tuple[str, ...] = ()
    allow_all_users: bool = True

    # Reverse-proxy / advertised-URL hint. If set, overrides all auto-detection.
    explicit_public_url: str = ""

    @classmethod
    def from_env(cls) -> BridgeConfig:
        token = os.getenv("EVEN_G2_BRIDGE_TOKEN", "").strip()
        host = os.getenv("EVEN_G2_BRIDGE_HOST", "127.0.0.1").strip()
        port = int(os.getenv("EVEN_G2_BRIDGE_PORT", "8767").strip() or "8767")
        public = os.getenv("EVEN_G2_BRIDGE_PUBLIC_URL", "").strip()
        serve_port = int(os.getenv("EVEN_G2_BRIDGE_SERVE_PORT", "8443").strip() or "8443")
        net_mode = os.getenv("EVEN_G2_BRIDGE_NET", "tailscale").strip().lower()
        if net_mode not in {"tailscale", "reverse-proxy", "lan"}:
            net_mode = "tailscale"

        asr_litellm_model = os.getenv("EVEN_G2_ASR_LITELLM_MODEL", "").strip()
        asr_litellm_base_url = os.getenv(
            "EVEN_G2_ASR_LITELLM_BASE_URL",
            os.getenv("LITELLM_BASE_URL", ""),
        ).strip()
        asr_litellm_api_key = os.getenv(
            "EVEN_G2_ASR_LITELLM_API_KEY",
            os.getenv("LITELLM_API_KEY", ""),
        ).strip()
        asr_sidecar_bin = os.getenv("EVEN_G2_ASR_SIDECAR_BIN", "").strip()

        allowed = tuple(
            u.strip()
            for u in os.getenv("EVEN_G2_ALLOWED_USERS", "").split(",")
            if u.strip()
        )
        allow_all = os.getenv("EVEN_G2_ALLOW_ALL_USERS", "1").strip() in {"1", "true", "yes"}

        return cls(
            token=token,
            ws_host=host,
            ws_port=port,
            public_url=public,
            net_mode=net_mode,
            tailscale_serve_port=serve_port,
            asr_litellm_model=asr_litellm_model,
            asr_litellm_base_url=asr_litellm_base_url,
            asr_litellm_api_key=asr_litellm_api_key,
            asr_sidecar_bin=asr_sidecar_bin,
            allowed_users=allowed,
            allow_all_users=allow_all,
            explicit_public_url=public,
        )

    @property
    def advertised_url(self) -> str:
        """The URL advertised in QR codes and CLI output."""
        from byoa_plugin.net import resolve_advertised_url  # noqa: PLC0415

        return resolve_advertised_url(self)
