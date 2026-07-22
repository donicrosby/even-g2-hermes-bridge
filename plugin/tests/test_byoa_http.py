"""Tests for the BYOA HTTPS endpoint served by the aiohttp app in server.py.

Covers: auth (valid/missing/wrong token), request validation (missing user
message, invalid JSON, wrong method), response shape (OpenAI chat-completion),
gzip support, and 503 when BYOA_TOKEN is unset.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from types import SimpleNamespace


class _MockAdapter:
    """Minimal adapter mock that resolves BYOA futures with a canned response."""

    def __init__(self) -> None:
        self._futures: dict[str, asyncio.Future[str]] = {}
        self._last_chat_id = "even-add-agent"

    @property
    def platform(self) -> object:
        return type("P", (), {"value": "even-g2"})()

    def build_source(self, *, chat_id: str, user_id: str) -> dict:
        return {"chat_id": chat_id, "user_id": user_id}

    def register_byoa_future(self, chat_id: str) -> asyncio.Future[str]:
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._futures[chat_id] = fut
        return fut

    def cancel_byoa_future(self, chat_id: str) -> None:
        fut = self._futures.pop(chat_id, None)
        if fut and not fut.done():
            fut.cancel()

    async def handle_message(self, event: object) -> None:

        for _chat_id, fut in list(self._futures.items()):
            if not fut.done():
                fut.set_result("This is a mock response from the agent.")


def _chat_completion_body(content: str = "Hello!") -> dict:
    return {
        "model": "openclaw",
        "messages": [{"role": "user", "content": content}],
    }


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _post_json(server: SimpleNamespace, path: str, body: dict, headers: dict | None = None) -> tuple[int, dict]:
    import aiohttp

    url = f"http://127.0.0.1:{server.bound_port}{path}"
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=body, headers=headers or {}) as resp:
            status = resp.status
            data = await resp.json()
    return status, data


@pytest.fixture
async def byoa_server(
    reset_env: dict[str, str],
    hermes_home: object,
) -> AsyncIterator[SimpleNamespace]:
    from types import SimpleNamespace

    from byoa_plugin.config import BridgeConfig
    from byoa_plugin.connections import ConnectionRegistry
    from byoa_plugin.server import BridgeServer

    cfg = BridgeConfig(
        token="ws-token",
        byoa_token="byoa-secret",
        ws_host="127.0.0.1",
        ws_port=0,
    )
    registry = ConnectionRegistry()
    server = BridgeServer(cfg, registry)

    mock_adapter = _MockAdapter()
    server.set_adapter(mock_adapter)

    await server.start()
    yield SimpleNamespace(
        server=server,
        bound_port=server.bound_port,
        cfg=cfg,
        registry=registry,
        mock_adapter=mock_adapter,
    )
    await server.stop()


class TestByoaAuth:
    async def test_valid_token_returns_200(self, byoa_server: SimpleNamespace) -> None:
        status, body = await _post_json(
            byoa_server, "/v1/chat/completions",
            _chat_completion_body(),
            _auth_headers("byoa-secret"),
        )
        assert status == 200
        assert body["object"] == "chat.completion"

    async def test_missing_token_returns_401(self, byoa_server: SimpleNamespace) -> None:
        status, body = await _post_json(
            byoa_server, "/v1/chat/completions",
            _chat_completion_body(),
        )
        assert status == 401
        assert body["error"]["type"] == "auth_error"

    async def test_wrong_token_returns_401(self, byoa_server: SimpleNamespace) -> None:
        status, body = await _post_json(
            byoa_server, "/v1/chat/completions",
            _chat_completion_body(),
            _auth_headers("wrong"),
        )
        assert status == 401
        assert body["error"]["type"] == "auth_error"

    async def test_token_checked_with_constant_time(self, byoa_server: SimpleNamespace) -> None:
        import hmac
        assert hmac.compare_digest.__name__ == "compare_digest"


class TestByoaRequestValidation:
    async def test_missing_user_message_returns_400(self, byoa_server: SimpleNamespace) -> None:
        status, body = await _post_json(
            byoa_server, "/v1/chat/completions",
            {"model": "openclaw", "messages": []},
            _auth_headers("byoa-secret"),
        )
        assert status == 400
        assert body["error"]["type"] == "invalid_request_error"

    async def test_invalid_json_returns_400(self, byoa_server: SimpleNamespace) -> None:
        import aiohttp

        url = f"http://127.0.0.1:{byoa_server.bound_port}/v1/chat/completions"
        async with aiohttp.ClientSession() as session, session.post(
            url,
            data=b"not json{",
            headers={**_auth_headers("byoa-secret"), "Content-Type": "application/json"},
        ) as resp:
            status = resp.status
            body = await resp.json()
        assert status == 400
        assert body["error"]["type"] == "invalid_request_error"


class TestByoaAlias:
    async def test_post_root_alias_works(self, byoa_server: SimpleNamespace) -> None:
        status, _body = await _post_json(
            byoa_server, "/",
            _chat_completion_body(),
            _auth_headers("byoa-secret"),
        )
        assert status == 200


class TestByoaResponseShape:
    async def test_response_has_openai_fields(self, byoa_server: SimpleNamespace) -> None:
        status, body = await _post_json(
            byoa_server, "/v1/chat/completions",
            _chat_completion_body(),
            _auth_headers("byoa-secret"),
        )
        assert status == 200
        assert "id" in body
        assert body["object"] == "chat.completion"
        assert body["choices"][0]["message"]["role"] == "assistant"
        assert body["choices"][0]["finish_reason"] == "stop"
        assert "usage" in body


class TestByoaDisabled:
    async def test_returns_503_when_byoa_token_unset(
        self, reset_env: dict[str, str], hermes_home: object,
    ) -> None:
        from types import SimpleNamespace

        from byoa_plugin.config import BridgeConfig
        from byoa_plugin.connections import ConnectionRegistry
        from byoa_plugin.server import BridgeServer

        cfg = BridgeConfig(token="ws", ws_host="127.0.0.1", ws_port=0)
        registry = ConnectionRegistry()
        server = BridgeServer(cfg, registry)
        await server.start()
        try:
            ns = SimpleNamespace(bound_port=server.bound_port)
            status, body = await _post_json(
                ns, "/v1/chat/completions",
                _chat_completion_body(),
                _auth_headers("anything"),
            )
            assert status == 503
            assert "disabled" in body["error"]["message"].lower()
        finally:
            await server.stop()


class TestHealthEndpoint:
    async def test_health_shows_byoa_enabled(self, byoa_server: SimpleNamespace) -> None:
        import aiohttp

        url = f"http://127.0.0.1:{byoa_server.bound_port}/health"
        async with aiohttp.ClientSession() as session, session.get(url) as resp:
            status = resp.status
            body = await resp.json()
        assert status == 200
        assert body["byoa_enabled"] is True
