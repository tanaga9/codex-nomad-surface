from __future__ import annotations

import html
from http.cookies import SimpleCookie
import hmac
import mimetypes
import re
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import streamlit as st
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from settings import AppSettings, configured_secret, load_settings


AUTH_COOKIE_NAME = "codex_nomad_auth"
AUTH_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 14
AUTH_RATE_LIMIT_WINDOW_SECONDS = 60
AUTH_RATE_LIMIT_MAX_FAILURES = 5
AUTH_RATE_LIMIT_LOCK_SECONDS = 60
AUTH_FAILURES_BY_CLIENT: dict[str, list[float]] = {}
STREAMLIT_RESERVED_PATH_PREFIXES = (
    "/_stcore/",
    "/media/",
    "/component/",
    "/static/",
)


def auth_required() -> bool:
    return configured_secret() != ""


def app_server_url_allows_file_content_route(app_server_url: str) -> bool:
    try:
        hostname = urlparse(app_server_url).hostname
    except ValueError:
        return False
    return hostname == "127.0.0.1"


FILE_CONTENT_ROUTE_ENABLED = app_server_url_allows_file_content_route(
    load_settings().app_server_url
)


def sync_file_content_route_setting(settings: AppSettings) -> None:
    global FILE_CONTENT_ROUTE_ENABLED
    FILE_CONTENT_ROUTE_ENABLED = app_server_url_allows_file_content_route(
        settings.app_server_url
    )


def file_content_route_enabled() -> bool:
    return FILE_CONTENT_ROUTE_ENABLED


def file_content_target_from_url_path(url_path: str) -> tuple[Path, int | None] | None:
    if not url_path or url_path == "/":
        return None
    if any(url_path.startswith(prefix) for prefix in STREAMLIT_RESERVED_PATH_PREFIXES):
        return None

    target = "/" + unquote(url_path).lstrip("/")
    line_number = None
    line_match = re.search(r":([1-9][0-9]*)$", target)
    if line_match:
        line_number = int(line_match.group(1))
        target = target[: line_match.start()]
    return Path(target), line_number


def file_content_path_from_url_path(url_path: str) -> Path | None:
    target = file_content_target_from_url_path(url_path)
    if target is None:
        return None
    return target[0]


def auth_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(configured_secret(), salt="codex-nomad-session")


def create_auth_session_token() -> str:
    return auth_serializer().dumps(
        {
            "kind": "codex-nomad-session",
            "nonce": secrets.token_urlsafe(16),
            "created_at": int(time.time()),
        }
    )


def valid_auth_session_token(token: str | None) -> bool:
    if not auth_required():
        return True
    if not token:
        return False
    try:
        payload = auth_serializer().loads(
            token,
            max_age=AUTH_COOKIE_MAX_AGE_SECONDS,
        )
    except (BadSignature, SignatureExpired):
        return False
    return isinstance(payload, dict) and payload.get("kind") == "codex-nomad-session"


def streamlit_auth_cookie() -> str:
    try:
        value = st.context.cookies.get(AUTH_COOKIE_NAME)
    except Exception:
        return ""
    return str(value or "")


def cookie_auth_is_valid() -> bool:
    return valid_auth_session_token(streamlit_auth_cookie())


def auth_lock_remaining_seconds(key: str) -> int:
    now = time.time()
    failures = [
        item
        for item in AUTH_FAILURES_BY_CLIENT.get(key, [])
        if now - item < AUTH_RATE_LIMIT_WINDOW_SECONDS
    ]
    AUTH_FAILURES_BY_CLIENT[key] = failures
    if len(failures) < AUTH_RATE_LIMIT_MAX_FAILURES:
        return 0
    remaining = AUTH_RATE_LIMIT_LOCK_SECONDS - int(now - failures[-1])
    return max(0, remaining)


def record_auth_failure(key: str) -> int:
    now = time.time()
    failures = [
        item
        for item in AUTH_FAILURES_BY_CLIENT.get(key, [])
        if now - item < AUTH_RATE_LIMIT_WINDOW_SECONDS
    ]
    failures.append(now)
    AUTH_FAILURES_BY_CLIENT[key] = failures
    return auth_lock_remaining_seconds(key)


def clear_auth_failures(key: str) -> None:
    AUTH_FAILURES_BY_CLIENT.pop(key, None)


def auth_cookie_from_scope(scope: dict[str, Any]) -> str:
    headers = dict(scope.get("headers") or [])
    raw_cookie = headers.get(b"cookie", b"").decode("latin-1")
    cookie = SimpleCookie()
    try:
        cookie.load(raw_cookie)
    except Exception:
        return ""
    morsel = cookie.get(AUTH_COOKIE_NAME)
    return morsel.value if morsel else ""


def rate_limit_key_from_scope(scope: dict[str, Any]) -> str:
    client = scope.get("client")
    if isinstance(client, tuple) and client:
        return str(client[0] or "unknown")
    return "unknown"


async def request_body(receive: Any, max_bytes: int = 4096) -> bytes:
    chunks: list[bytes] = []
    total = 0
    more_body = True
    while more_body:
        message = await receive()
        if message.get("type") != "http.request":
            break
        chunk = message.get("body", b"")
        total += len(chunk)
        if total > max_bytes:
            return b""
        chunks.append(chunk)
        more_body = bool(message.get("more_body"))
    return b"".join(chunks)


class FileContentMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request_path = str(scope.get("path") or "")
        if request_path == "/_nomad_auth/login":
            await self._handle_login(scope, receive, send)
            return

        target = file_content_target_from_url_path(request_path)
        if auth_required() and not valid_auth_session_token(auth_cookie_from_scope(scope)):
            if file_content_route_enabled() and target is not None:
                await self._send_response(
                    send,
                    b"Authentication required.",
                    "text/plain",
                    status=401,
                )
                return
            await self._send_redirect(send, "/_nomad_auth/login")
            return

        if not file_content_route_enabled():
            await self.app(scope, receive, send)
            return

        if target is None:
            await self.app(scope, receive, send)
            return

        target_path, line_number = target
        if not target_path.exists():
            await self.app(scope, receive, send)
            return

        if target_path.is_dir():
            await self._send_response(
                send,
                b"",
                "text/plain; charset=utf-8",
                line_number=line_number,
            )
            return

        if not target_path.is_file():
            await self.app(scope, receive, send)
            return

        try:
            content = target_path.read_bytes()
        except OSError:
            await self.app(scope, receive, send)
            return

        content_type = (
            mimetypes.guess_type(target_path.name)[0] or "application/octet-stream"
        )
        await self._send_response(
            send,
            content,
            content_type,
            line_number=line_number,
        )

    async def _send_response(
        self,
        send: Any,
        content: bytes,
        content_type: str,
        line_number: int | None = None,
        status: int = 200,
    ) -> None:
        headers = [
            (b"content-length", str(len(content)).encode("ascii")),
            (b"content-type", content_type.encode("latin-1")),
        ]
        if line_number is not None:
            headers.append((b"x-nomad-file-line", str(line_number).encode("ascii")))
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": headers,
            }
        )
        await send({"type": "http.response.body", "body": content})

    async def _handle_login(
        self, scope: dict[str, Any], receive: Any, send: Any
    ) -> None:
        if not auth_required() or valid_auth_session_token(auth_cookie_from_scope(scope)):
            await self._send_redirect(send, "/")
            return

        method = str(scope.get("method") or "GET").upper()
        if method == "GET":
            query = parse_qs(str(scope.get("query_string") or b"", "latin-1"))
            await self._send_login_page(
                send,
                auth_error=bool(query.get("auth_error")),
                auth_locked=bool(query.get("auth_locked")),
            )
            return
        if method != "POST":
            await self._send_response(
                send,
                b"Method not allowed.",
                "text/plain",
                status=405,
            )
            return

        rate_limit_key = rate_limit_key_from_scope(scope)
        if auth_lock_remaining_seconds(rate_limit_key) > 0:
            await self._send_redirect(send, "/_nomad_auth/login?auth_locked=1")
            return

        body = await request_body(receive)
        form = parse_qs(body.decode("utf-8", errors="replace"))
        secret = str((form.get("secret") or [""])[0])
        if not hmac.compare_digest(secret, configured_secret()):
            remaining = record_auth_failure(rate_limit_key)
            location = (
                "/_nomad_auth/login?auth_locked=1"
                if remaining > 0
                else "/_nomad_auth/login?auth_error=1"
            )
            await self._send_redirect(send, location)
            return

        clear_auth_failures(rate_limit_key)
        await self._send_redirect(
            send,
            "/",
            set_cookie=self._auth_cookie_header(scope),
        )

    async def _send_login_page(
        self,
        send: Any,
        auth_error: bool = False,
        auth_locked: bool = False,
    ) -> None:
        message = ""
        if auth_locked:
            message = "Too many failed attempts. Try again in one minute."
        elif auth_error:
            message = "The secret does not match."
        message_html = (
            f'<div class="error">{html.escape(message)}</div>' if message else ""
        )
        body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Codex Nomad Surface</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      font: 16px system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #0f1117;
      color: #e5e7eb;
    }}
    main {{
      width: min(92vw, 360px);
    }}
    h1 {{
      margin: 0 0 0.5rem;
      font-size: 1.45rem;
      font-weight: 650;
    }}
    p {{
      margin: 0 0 1rem;
      color: #9ca3af;
      line-height: 1.45;
    }}
    form {{
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
    }}
    input, button {{
      font: inherit;
      min-height: 2.6rem;
      border-radius: 0.35rem;
    }}
    input {{
      border: 1px solid #303846;
      padding: 0 0.7rem;
      background: #171b23;
      color: #f9fafb;
      outline: none;
    }}
    input:focus {{
      border-color: #6b7280;
      box-shadow: 0 0 0 3px rgba(107, 114, 128, 0.22);
    }}
    input::placeholder {{
      color: #6b7280;
    }}
    button {{
      border: 0;
      padding: 0 0.75rem;
      color: #0f1117;
      background: #e5e7eb;
      cursor: pointer;
    }}
    button:hover {{
      background: #f9fafb;
    }}
    .error {{
      margin: 0 0 0.75rem;
      color: #fca5a5;
      font-size: 0.95rem;
    }}
  </style>
</head>
<body>
  <main>
    <h1>Codex Nomad Surface</h1>
    <p>Unlock this local operation surface.</p>
    {message_html}
    <form method="post" action="/_nomad_auth/login">
      <input name="secret" type="password" autocomplete="current-password" placeholder="Enter the local secret" autofocus>
      <button type="submit">Unlock</button>
    </form>
  </main>
</body>
</html>""".encode("utf-8")
        await self._send_response(send, body, "text/html; charset=utf-8")

    def _auth_cookie_header(self, scope: dict[str, Any]) -> bytes:
        secure = "; Secure" if scope.get("scheme") == "https" else ""
        return (
            f"{AUTH_COOKIE_NAME}={create_auth_session_token()}; "
            f"Max-Age={AUTH_COOKIE_MAX_AGE_SECONDS}; Path=/; "
            f"SameSite=Lax; HttpOnly{secure}"
        ).encode("latin-1")

    async def _send_redirect(
        self,
        send: Any,
        location: str,
        set_cookie: bytes | None = None,
    ) -> None:
        headers = [(b"location", location.encode("latin-1"))]
        if set_cookie is not None:
            headers.append((b"set-cookie", set_cookie))
        await send(
            {
                "type": "http.response.start",
                "status": 303,
                "headers": headers,
            }
        )
        await send({"type": "http.response.body", "body": b""})
