"""Tests for BridgeConfig.from_env — env-var-driven configuration.

Pins defaults, parsing edge cases (whitespace, invalid net_mode), and the
allowed_users CSV format. Uses reset_env fixture to prevent leakage.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from byoa_plugin.config import BridgeConfig

if TYPE_CHECKING:
    from pathlib import Path


class TestDefaults:
    def test_defaults_when_no_env_set(
        self, reset_env: dict[str, str], hermes_home: Path,
    ) -> None:
        cfg = BridgeConfig.from_env()
        assert cfg.token == ""
        assert cfg.ws_host == "127.0.0.1"
        assert cfg.ws_port == 8767
        assert cfg.public_url == ""
        assert cfg.net_mode == "tailscale"
        assert cfg.tailscale_serve_port == 8443
        assert cfg.allow_all_users is True
        assert cfg.allowed_users == ()

    def test_asr_fields_default_empty(
        self, reset_env: dict[str, str], hermes_home: Path,
    ) -> None:
        cfg = BridgeConfig.from_env()
        assert cfg.asr_litellm_model == ""
        assert cfg.asr_litellm_base_url == ""
        assert cfg.asr_litellm_api_key == ""
        assert cfg.asr_sidecar_bin == ""


class TestTokenParsing:
    def test_token_read_from_env(
        self, reset_env: dict[str, str], hermes_home: Path,
    ) -> None:
        os.environ["EVEN_G2_BRIDGE_TOKEN"] = "secret-token-123"  # noqa: S105
        cfg = BridgeConfig.from_env()
        assert cfg.token == "secret-token-123"  # noqa: S105

    def test_token_whitespace_stripped(
        self, reset_env: dict[str, str], hermes_home: Path,
    ) -> None:
        os.environ["EVEN_G2_BRIDGE_TOKEN"] = "  token-with-spaces  \n"  # noqa: S105
        cfg = BridgeConfig.from_env()
        assert cfg.token == "token-with-spaces"  # noqa: S105


class TestHostAndPort:
    def test_host_read_from_env(
        self, reset_env: dict[str, str], hermes_home: Path,
    ) -> None:
        os.environ["EVEN_G2_BRIDGE_HOST"] = "0.0.0.0"  # noqa: S104
        cfg = BridgeConfig.from_env()
        assert cfg.ws_host == "0.0.0.0"  # noqa: S104

    def test_port_read_from_env(
        self, reset_env: dict[str, str], hermes_home: Path,
    ) -> None:
        os.environ["EVEN_G2_BRIDGE_PORT"] = "9999"
        cfg = BridgeConfig.from_env()
        assert cfg.ws_port == 9999

    def test_port_empty_string_falls_back_to_default(
        self, reset_env: dict[str, str], hermes_home: Path,
    ) -> None:
        os.environ["EVEN_G2_BRIDGE_PORT"] = "   "
        cfg = BridgeConfig.from_env()
        assert cfg.ws_port == 8767


class TestNetMode:
    @pytest.mark.parametrize("mode", ["tailscale", "reverse-proxy", "lan"])
    def test_valid_net_modes_accepted(
        self, reset_env: dict[str, str], hermes_home: Path, mode: str,
    ) -> None:
        os.environ["EVEN_G2_BRIDGE_NET"] = mode
        cfg = BridgeConfig.from_env()
        assert cfg.net_mode == mode

    def test_invalid_net_mode_falls_back_to_tailscale(
        self, reset_env: dict[str, str], hermes_home: Path,
    ) -> None:
        os.environ["EVEN_G2_BRIDGE_NET"] = "magic-tunnel"
        cfg = BridgeConfig.from_env()
        assert cfg.net_mode == "tailscale"

    def test_net_mode_case_insensitive(
        self, reset_env: dict[str, str], hermes_home: Path,
    ) -> None:
        os.environ["EVEN_G2_BRIDGE_NET"] = "LAN"
        cfg = BridgeConfig.from_env()
        assert cfg.net_mode == "lan"


class TestAllowedUsers:
    def test_single_user(
        self, reset_env: dict[str, str], hermes_home: Path,
    ) -> None:
        os.environ["EVEN_G2_ALLOWED_USERS"] = "alice"
        cfg = BridgeConfig.from_env()
        assert cfg.allowed_users == ("alice",)

    def test_multiple_users_csv(
        self, reset_env: dict[str, str], hermes_home: Path,
    ) -> None:
        os.environ["EVEN_G2_ALLOWED_USERS"] = "alice,bob,carol"
        cfg = BridgeConfig.from_env()
        assert cfg.allowed_users == ("alice", "bob", "carol")

    def test_whitespace_trimmed_around_users(
        self, reset_env: dict[str, str], hermes_home: Path,
    ) -> None:
        os.environ["EVEN_G2_ALLOWED_USERS"] = "  alice , bob  ,  carol  "
        cfg = BridgeConfig.from_env()
        assert cfg.allowed_users == ("alice", "bob", "carol")

    def test_empty_entries_dropped(
        self, reset_env: dict[str, str], hermes_home: Path,
    ) -> None:
        os.environ["EVEN_G2_ALLOWED_USERS"] = "alice,,bob,"
        cfg = BridgeConfig.from_env()
        assert cfg.allowed_users == ("alice", "bob")


class TestAllowAllUsers:
    @pytest.mark.parametrize("value", ["1", "true", "yes"])
    def test_truthy_values_enable_allow_all(
        self, reset_env: dict[str, str], hermes_home: Path, value: str,
    ) -> None:
        os.environ["EVEN_G2_ALLOW_ALL_USERS"] = value
        cfg = BridgeConfig.from_env()
        assert cfg.allow_all_users is True

    def test_other_values_disable_allow_all(
        self, reset_env: dict[str, str], hermes_home: Path,
    ) -> None:
        os.environ["EVEN_G2_ALLOW_ALL_USERS"] = "0"
        cfg = BridgeConfig.from_env()
        assert cfg.allow_all_users is False


class TestAsrConfig:
    def test_litellm_config_read_from_env(
        self, reset_env: dict[str, str], hermes_home: Path,
    ) -> None:
        os.environ["EVEN_G2_ASR_LITELLM_MODEL"] = "whisper"
        os.environ["EVEN_G2_ASR_LITELLM_BASE_URL"] = "https://litellm.local"
        os.environ["EVEN_G2_ASR_LITELLM_API_KEY"] = "test-key-xxxxx"
        cfg = BridgeConfig.from_env()
        assert cfg.asr_litellm_model == "whisper"
        assert cfg.asr_litellm_base_url == "https://litellm.local"
        assert cfg.asr_litellm_api_key == "test-key-xxxxx"

    def test_litellm_falls_back_to_generic_env(
        self, reset_env: dict[str, str], hermes_home: Path,
    ) -> None:
        os.environ["LITELLM_BASE_URL"] = "https://litellm.example.com"
        os.environ["LITELLM_API_KEY"] = "test-key-generic"
        cfg = BridgeConfig.from_env()
        assert cfg.asr_litellm_base_url == "https://litellm.example.com"
        assert cfg.asr_litellm_api_key == "test-key-generic"

    def test_litellm_specific_env_overrides_generic(
        self, reset_env: dict[str, str], hermes_home: Path,
    ) -> None:
        os.environ["LITELLM_BASE_URL"] = "https://generic.example.com"
        os.environ["EVEN_G2_ASR_LITELLM_BASE_URL"] = "https://specific.example.com"
        cfg = BridgeConfig.from_env()
        assert cfg.asr_litellm_base_url == "https://specific.example.com"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
