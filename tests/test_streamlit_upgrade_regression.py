import asyncio
from pathlib import Path
from typing import Any

import pytest
import streamlit

from codex_nomad_surface.http_gate import FileContentMiddleware


async def _middleware_messages(
    middleware: FileContentMiddleware, path: str
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "scheme": "http",
        "client": ("127.0.0.1", 12345),
    }
    await middleware(scope, receive, send)
    return messages


def test_streamlit_version_matches_supported_upgrade_target() -> None:
    assert streamlit.__version__ == "1.59.2"


def test_unauthenticated_file_route_does_not_expose_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import codex_nomad_surface.http_gate as http_gate

    target = tmp_path / "private.txt"
    target.write_text("private content", encoding="utf-8")
    monkeypatch.setattr(http_gate, "auth_required", lambda: True)
    monkeypatch.setattr(http_gate, "file_content_route_enabled", lambda: True)
    monkeypatch.setattr(http_gate, "valid_auth_session_token", lambda token: False)

    messages = asyncio.run(
        _middleware_messages(FileContentMiddleware(app=None), str(target))
    )

    start = next(message for message in messages if message["type"] == "http.response.start")
    body = next(message["body"] for message in messages if "body" in message)
    assert start["status"] == 401
    assert body == b"Authentication required."
