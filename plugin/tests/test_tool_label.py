"""Characterization tests for hooks.tool_label.

Tool label generation is the user-visible rendering of agent tool calls on
the HUD. Behavior must be preserved across refactors.
"""

from __future__ import annotations

from byoa_plugin.hooks import tool_label


class TestToolLabel:
    def test_extracts_query_arg(self) -> None:
        assert tool_label("web_search", {"query": "weather today"}) == "web_search: weather today"

    def test_extracts_command_arg(self) -> None:
        assert tool_label("run_command", {"command": "ls -la /tmp"}) == "run_command: ls -la /tmp"

    def test_extracts_path_arg(self) -> None:
        assert tool_label("read_file", {"path": "/etc/hosts"}) == "read_file: /etc/hosts"

    def test_extracts_url_arg(self) -> None:
        assert tool_label("fetch", {"url": "https://example.com"}) == "fetch: https://example.com"

    def test_extracts_first_salient_key_when_multiple_present(self) -> None:
        # Order: command, query, path, url, file, code, name, input
        result = tool_label("tool", {"command": "x", "query": "y"})
        assert result == "tool: x"

    def test_truncates_long_args_to_max_label_len(self) -> None:
        long_arg = "x" * 100
        result = tool_label("read_file", {"path": long_arg})
        assert len(result) <= 64
        assert result.startswith("read_file: x")
        assert result.endswith("...")

    def test_falls_back_to_bare_tool_name_when_no_salient_arg(self) -> None:
        assert tool_label("unknown_tool", {"foo": "bar"}) == "unknown_tool"

    def test_falls_back_when_args_is_none(self) -> None:
        assert tool_label("tool", None) == "tool"

    def test_falls_back_when_args_is_empty_dict(self) -> None:
        assert tool_label("tool", {}) == "tool"

    def test_falls_back_when_salient_arg_is_empty_string(self) -> None:
        assert tool_label("tool", {"query": ""}) == "tool"

    def test_falls_back_when_salient_arg_is_whitespace(self) -> None:
        assert tool_label("tool", {"query": "   "}) == "tool"

    def test_collapses_newlines_in_arg_value(self) -> None:
        result = tool_label("run", {"command": "echo hello\necho world"})
        assert "\n" not in result
        assert "echo hello echo world" in result

    def test_truncates_tool_name_if_too_long(self) -> None:
        long_name = "x" * 100
        result = tool_label(long_name, None)
        assert len(result) <= 64
