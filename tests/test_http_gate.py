import asyncio
from typing import Any

import pytest

from codex_nomad_surface.http_gate import FileContentMiddleware


async def _login_page_body() -> str:
    messages: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    middleware = FileContentMiddleware(app=None)
    await middleware._send_login_page(send)
    body = next(message["body"] for message in messages if "body" in message)
    return body.decode("utf-8")


def test_login_page_hides_dummy_username_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NOMAD_AUTH_DUMMY_USERNAME_FIELD", raising=False)

    body = asyncio.run(_login_page_body())

    assert 'name="username"' not in body
    assert 'name="secret"' in body
    assert 'autocomplete="current-password"' in body


def test_login_page_can_show_dummy_username_for_password_managers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOMAD_AUTH_DUMMY_USERNAME_FIELD", "1")

    body = asyncio.run(_login_page_body())

    assert 'name="username"' in body
    assert 'autocomplete="username"' in body
    assert 'value="codex"' in body
    assert 'name="secret"' in body


def test_login_post_ignores_dummy_username(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOMAD_AUTH_SECRET", "expected-secret")
    messages: list[dict[str, Any]] = []
    request_messages = [
        {
            "type": "http.request",
            "body": b"username=anything&secret=expected-secret",
            "more_body": False,
        }
    ]

    async def receive() -> dict[str, Any]:
        return request_messages.pop(0)

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    middleware = FileContentMiddleware(app=None)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/_nomad_auth/login",
        "scheme": "http",
        "client": ("127.0.0.1", 12345),
    }

    asyncio.run(middleware._handle_login(scope, receive, send))

    start = next(
        message for message in messages if message["type"] == "http.response.start"
    )
    headers = dict(start["headers"])
    assert start["status"] == 303
    assert headers[b"location"] == b"/"
    assert b"set-cookie" in headers
