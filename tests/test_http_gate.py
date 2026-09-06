import asyncio
import os
from typing import Any
from urllib.parse import quote

import pytest

from codex_nomad_surface.http_gate import (
    FileContentMiddleware,
    file_content_target_from_scope,
    file_content_target_from_url_path,
)


def test_windows_file_content_route_decodes_absolute_path_and_line() -> None:
    target = file_content_target_from_scope(
        {
            "path": "/_nomad_file",
            "query_string": b"path=C%3A%5CUsers%5Cperson%5Crepo%5Capp.py%3A12",
        }
    )

    assert target is not None
    path, line_number = target
    assert str(path) == r"C:\Users\person\repo\app.py"
    assert line_number == 12


def test_windows_file_content_route_rejects_relative_path() -> None:
    assert (
        file_content_target_from_scope(
            {"path": "/_nomad_file", "query_string": b"path=relative.txt"}
        )
        is None
    )


@pytest.mark.skipif(os.name != "nt", reason="requires Windows drive-letter paths")
def test_browser_normalized_windows_url_path_decodes_as_drive_path() -> None:
    target = file_content_target_from_url_path(
        "/C:/Users/person/repo/file%20with%20spaces.py:14"
    )

    assert target is not None
    path, line_number = target
    assert str(path) == r"C:\Users\person\repo\file with spaces.py"
    assert line_number == 14


@pytest.mark.skipif(os.name != "nt", reason="requires Windows drive-letter paths")
def test_windows_file_content_route_serves_file(tmp_path, monkeypatch) -> None:
    source = tmp_path / "file with spaces.txt"
    source.write_text("Windows preview", encoding="utf-8")
    messages: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    async def downstream(scope, receive, send) -> None:
        raise AssertionError("request unexpectedly reached Streamlit")

    monkeypatch.setattr(
        "codex_nomad_surface.http_gate.file_content_route_enabled", lambda: True
    )
    monkeypatch.setattr("codex_nomad_surface.http_gate.auth_required", lambda: False)
    middleware = FileContentMiddleware(downstream)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/_nomad_file",
        "query_string": f"path={quote(str(source), safe='')}".encode("ascii"),
        "headers": [],
    }

    asyncio.run(middleware(scope, None, send))

    start = next(
        message for message in messages if message["type"] == "http.response.start"
    )
    body = next(
        message["body"]
        for message in messages
        if message["type"] == "http.response.body"
    )
    assert start["status"] == 200
    assert body == b"Windows preview"


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


def test_auth_cookie_is_secure_behind_an_https_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOMAD_AUTH_SECRET", "expected-secret")
    monkeypatch.delenv("NOMAD_AUTH_SESSION_DAYS", raising=False)
    middleware = FileContentMiddleware(app=None)

    cookie = middleware._auth_cookie_header(
        {
            "scheme": "http",
            "headers": [(b"x-forwarded-proto", b"https")],
        }
    )

    assert b"Max-Age=15552000" in cookie
    assert b"Expires=" in cookie
    assert b"; Secure" in cookie
