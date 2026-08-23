from __future__ import annotations

import asyncio
import json
import queue
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from starlette.websockets import WebSocket, WebSocketDisconnect

from codex_nomad_surface.http_gate import (
    auth_cookie_from_scope,
    auth_required,
    valid_auth_session_token,
)
from codex_nomad_surface.text_authoring import scope_text_snapshot
from codex_nomad_surface.text_store import (
    TextRevisionConflict,
    load_text_snapshot,
    read_text_manifest,
    save_text,
    save_text_export,
    text_exists,
    text_editor_kind,
    text_file_references,
    text_manifest_for_thread,
    update_text_presentation,
)


TEXT_TOOL_TIMEOUT_SECONDS = 20.0
TEXT_REPLACED_CLOSE_CODE = 4001
TEXT_READ_MAX_CHARS = 80_000
TEXT_DEVELOPER_INSTRUCTIONS = (
    "This thread uses a Nomad Surface embedded Text or Document editor. Use the text "
    "dynamic tools as the primary interface for the managed document. Read the "
    "selection or narrowest relevant scope before editing, then pass the returned "
    "revision to text.apply_patch. Prefer small, targeted operations and preserve "
    "the document's plain-text or Markdown source. Do not edit the backing file "
    "directly. If a revision conflict occurs, read again and re-plan."
)


@dataclass
class PendingTextRequest:
    method: str
    arguments: dict[str, Any]
    event: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None


@dataclass
class TextConnection:
    outgoing: queue.Queue[dict[str, Any] | None] = field(default_factory=queue.Queue)
    retired: threading.Event = field(default_factory=threading.Event)


class TextBroker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._connections: dict[str, TextConnection] = {}
        self._pending: dict[str, PendingTextRequest] = {}

    def register(self, text_id: str) -> TextConnection:
        connection = TextConnection()
        with self._lock:
            previous = self._connections.pop(text_id, None)
            if previous:
                previous.retired.set()
                previous.outgoing.put(None)
            self._connections[text_id] = connection
        return connection

    def is_current(self, text_id: str, connection: TextConnection) -> bool:
        with self._lock:
            return self._connections.get(text_id) is connection

    def unregister(self, text_id: str, connection: TextConnection) -> None:
        with self._lock:
            if self._connections.get(text_id) is connection:
                self._connections.pop(text_id, None)
        connection.outgoing.put(None)

    def call(
        self, text_id: str, method: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        pending = PendingTextRequest(method=method, arguments=arguments or {})
        with self._lock:
            connection = self._connections.get(text_id)
            if not connection:
                raise RuntimeError("text_unavailable")
            self._pending[request_id] = pending
            connection.outgoing.put(
                {
                    "type": "request",
                    "request": {
                        "id": request_id,
                        "method": method,
                        "arguments": arguments or {},
                    },
                }
            )
        if not pending.event.wait(TEXT_TOOL_TIMEOUT_SECONDS):
            with self._lock:
                self._pending.pop(request_id, None)
            raise TimeoutError("text_timeout")
        return pending.result or {"ok": False, "error": "text_no_result"}

    def resolve(self, request_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            pending = self._pending.pop(request_id, None)
        if pending:
            pending.result = result
            pending.event.set()

    def has_pending(self, request_id: str) -> bool:
        with self._lock:
            return request_id in self._pending

    def request_context(self, request_id: str) -> tuple[str, dict[str, Any]] | None:
        with self._lock:
            pending = self._pending.get(request_id)
            return (pending.method, pending.arguments) if pending else None


TEXT_BROKER = TextBroker()


async def _text_sender(websocket: WebSocket, connection: TextConnection) -> None:
    while True:
        message = await asyncio.to_thread(connection.outgoing.get)
        if message is None:
            if connection.retired.is_set():
                try:
                    await websocket.close(code=TEXT_REPLACED_CLOSE_CODE)
                except RuntimeError:
                    pass
            return
        await websocket.send_json(message)


async def text_websocket(websocket: WebSocket) -> None:
    if auth_required() and not valid_auth_session_token(
        auth_cookie_from_scope(websocket.scope)
    ):
        await websocket.close(code=4401)
        return
    text_id = str(websocket.path_params.get("text_id") or "")
    if not text_exists(text_id):
        await websocket.close(code=4404)
        return

    await websocket.accept()
    connection = TEXT_BROKER.register(text_id)
    try:
        manifest, content = await asyncio.to_thread(load_text_snapshot, text_id)
    except FileNotFoundError:
        TEXT_BROKER.unregister(text_id, connection)
        await websocket.close(code=4404)
        return
    connection.outgoing.put(
        {
            "type": "sync",
            "revision": int(manifest.get("current_revision") or 0),
            "content": content,
        }
    )
    sender = asyncio.create_task(_text_sender(websocket, connection))
    try:
        while True:
            message = await websocket.receive_json()
            if not TEXT_BROKER.is_current(text_id, connection):
                return
            if not isinstance(message, dict):
                continue
            message_type = str(message.get("type") or "")
            if message_type == "presentation":
                presentation = str(message.get("presentation") or "")
                try:
                    await asyncio.to_thread(
                        update_text_presentation, text_id, presentation
                    )
                except (FileNotFoundError, ValueError):
                    pass
                continue
            if message_type == "checkpoint":
                content = message.get("content")
                revision = message.get("base_revision")
                if not isinstance(content, str) or type(revision) is not int:
                    await websocket.send_json(
                        {"type": "checkpoint_ack", "ok": False, "error": "invalid_checkpoint"}
                    )
                    continue
                try:
                    manifest = await asyncio.to_thread(
                        save_text,
                        text_id,
                        content,
                        expected_revision=revision,
                    )
                    await websocket.send_json(
                        {
                            "type": "checkpoint_ack",
                            "ok": True,
                            "revision": int(manifest["current_revision"]),
                        }
                    )
                except TextRevisionConflict as exc:
                    current_manifest, canonical = await asyncio.to_thread(
                        load_text_snapshot, text_id
                    )
                    await websocket.send_json(
                        {
                            "type": "checkpoint_ack",
                            "ok": False,
                            "error": "revision_conflict",
                            "revision": int(
                                current_manifest.get("current_revision")
                                or exc.current_revision
                            ),
                            "content": canonical,
                        }
                    )
                except Exception as exc:
                    await websocket.send_json(
                        {"type": "checkpoint_ack", "ok": False, "error": f"save_failed: {exc}"}
                    )
                continue
            if message_type != "response":
                continue

            request_id = str(message.get("id") or "")
            if not request_id or not TEXT_BROKER.has_pending(request_id):
                connection.outgoing.put(
                    {
                        "type": "reject",
                        "id": request_id,
                        "error": "request_expired",
                    }
                )
                continue
            request_context = TEXT_BROKER.request_context(request_id)
            if request_context is None:
                continue
            request_method, request_arguments = request_context
            result = {
                "ok": bool(message.get("ok")),
                "error": str(message.get("error") or ""),
            }
            payload = message.get("payload")
            if request_method == "read" and result["ok"]:
                if not (
                    isinstance(payload, dict)
                    and isinstance(payload.get("content"), str)
                    and type(payload.get("revision")) is int
                ):
                    result = {"ok": False, "error": "text_invalid_response"}
                    payload = None
                else:
                    try:
                        manifest = read_text_manifest(text_id)
                        if not manifest:
                            raise FileNotFoundError("text_not_found")
                        editor_kind = text_editor_kind(manifest)
                        scope, scoped_text = scope_text_snapshot(
                            content=payload["content"],
                            arguments=request_arguments,
                            editor_kind=editor_kind,
                            selection=(
                                payload.get("selection")
                                if isinstance(payload.get("selection"), dict)
                                else None
                            ),
                            max_chars=TEXT_READ_MAX_CHARS,
                        )
                        payload = {
                            "text_id": text_id,
                            "editor_kind": editor_kind,
                            "format": manifest["format"],
                            "revision": payload["revision"],
                            "scope": scope,
                            "text": scoped_text,
                            "live": True,
                        }
                    except Exception as exc:
                        result = {"ok": False, "error": str(exc)}
                        payload = None
            if request_method == "apply_patch" and result["ok"] and not (
                isinstance(payload, dict) and isinstance(payload.get("content"), str)
            ):
                result = {"ok": False, "error": "text_invalid_response"}
                payload = None
            if request_method == "apply_patch" and result["ok"]:
                assert isinstance(payload, dict)
                base_revision = payload.get("base_revision")
                if type(base_revision) is not int:
                    result = {"ok": False, "error": "text_invalid_response"}
                else:
                    try:
                        manifest = await asyncio.to_thread(
                            save_text,
                            text_id,
                            payload["content"],
                            expected_revision=base_revision,
                        )
                        payload["revision"] = int(manifest["current_revision"])
                        connection.outgoing.put(
                            {
                                "type": "commit",
                                "id": request_id,
                                "revision": payload["revision"],
                                "content": payload["content"],
                            }
                        )
                    except TextRevisionConflict as exc:
                        current_manifest, current_content = await asyncio.to_thread(
                            load_text_snapshot, text_id
                        )
                        result = {"ok": False, "error": "revision_conflict"}
                        payload = {
                            "revision": int(
                                current_manifest.get("current_revision")
                                or exc.current_revision
                            ),
                            "content": current_content,
                        }
                    except Exception as exc:
                        result = {"ok": False, "error": f"save_failed: {exc}"}
                        payload = None
            if request_method in {"export_snapshot", "sync_snapshot"} and result["ok"]:
                if not (
                    isinstance(payload, dict)
                    and isinstance(payload.get("text"), str)
                    and type(payload.get("revision")) is int
                ):
                    result = {"ok": False, "error": "text_invalid_response"}
                    payload = None
                else:
                    try:
                        current_manifest, current_content = await asyncio.to_thread(
                            load_text_snapshot, text_id
                        )
                        if payload["text"] != current_content:
                            manifest = await asyncio.to_thread(
                                save_text,
                                text_id,
                                payload["text"],
                                expected_revision=payload["revision"],
                            )
                            payload["revision"] = int(manifest["current_revision"])
                        else:
                            payload["revision"] = int(
                                current_manifest.get("current_revision") or 0
                            )
                        connection.outgoing.put(
                            {
                                "type": "snapshot_commit",
                                "id": request_id,
                                "revision": payload["revision"],
                                "content": payload["text"],
                            }
                        )
                    except TextRevisionConflict as exc:
                        current_manifest, current_content = await asyncio.to_thread(
                            load_text_snapshot, text_id
                        )
                        result = {"ok": False, "error": "revision_conflict"}
                        payload = {
                            "revision": int(
                                current_manifest.get("current_revision")
                                or exc.current_revision
                            ),
                            "content": current_content,
                        }
                    except Exception as exc:
                        result = {"ok": False, "error": f"save_failed: {exc}"}
                        payload = None
            if request_method in {
                "apply_patch",
                "export_snapshot",
                "sync_snapshot",
            } and not result["ok"]:
                connection.outgoing.put(
                    {
                        "type": "reject",
                        "id": request_id,
                        "error": result["error"],
                        **(
                            {
                                "revision": payload["revision"],
                                "content": payload["content"],
                            }
                            if isinstance(payload, dict)
                            and type(payload.get("revision")) is int
                            and isinstance(payload.get("content"), str)
                            else {}
                        ),
                    }
                )
            if isinstance(payload, dict):
                payload.pop("content", None)
                payload.pop("base_revision", None)
                if request_method in {"export_snapshot", "sync_snapshot"}:
                    payload.pop("text", None)
                result["payload"] = payload
            TEXT_BROKER.resolve(request_id, result)
    except WebSocketDisconnect:
        pass
    finally:
        TEXT_BROKER.unregister(text_id, connection)
        sender.cancel()


def _closed_object(
    properties: dict[str, Any], required: list[str] | None = None
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def text_dynamic_tools() -> list[dict[str, Any]]:
    line = {"type": "integer", "minimum": 1, "maximum": 1_000_000}
    scope = {
        "type": "string",
        "enum": ["all", "selection", "lines", "section", "outline"],
    }
    targeted_operation = _closed_object(
        {
            "type": {
                "type": "string",
                "enum": ["replace", "insert_before", "insert_after", "delete"],
            },
            "from_line": line,
            "to_line": line,
            "old_text": {"type": "string", "maxLength": TEXT_READ_MAX_CHARS},
            "new_text": {"type": "string", "maxLength": TEXT_READ_MAX_CHARS},
        },
        ["type", "from_line", "old_text", "new_text"],
    )
    document_operation = _closed_object(
        {
            "type": {"const": "replace_document"},
            "old_text": {"type": "string", "maxLength": TEXT_READ_MAX_CHARS},
            "new_text": {"type": "string", "maxLength": TEXT_READ_MAX_CHARS},
        },
        ["type", "old_text", "new_text"],
    )
    operation = {"oneOf": [targeted_operation, document_operation]}
    return [
        {
            "type": "namespace",
            "name": "text",
            "description": "Read and edit the Text or Document content embedded in this thread.",
            "tools": [
                {
                    "type": "function",
                    "name": "read",
                    "description": "Read the selection, lines, section, outline, or full document.",
                    "inputSchema": _closed_object(
                        {"scope": scope, "from_line": line, "to_line": line, "heading": {"type": "string", "maxLength": 500}},
                        ["scope"],
                    ),
                },
                {
                    "type": "function",
                    "name": "apply_patch",
                    "description": (
                        "Apply a revision-checked batch of plain-text edits as one "
                        "undo step. Every operation range is resolved against the "
                        "same base revision; operations must not overlap."
                    ),
                    "inputSchema": _closed_object(
                        {
                            "base_revision": {"type": "integer", "minimum": 0},
                            "operations": {"type": "array", "items": operation, "minItems": 1, "maxItems": 50},
                        },
                        ["base_revision", "operations"],
                    ),
                },
                {
                    "type": "function",
                    "name": "export",
                    "description": "Save the current managed text to its export directory.",
                    "inputSchema": _closed_object({}),
                },
            ],
        }
    ]


def text_initial_context_items() -> list[dict[str, Any]]:
    return [
        {
            "type": "message",
            "role": "developer",
            "content": [
                {
                    "type": "input_text",
                    "text": TEXT_DEVELOPER_INSTRUCTIONS,
                }
            ],
        }
    ]


def _content_result(success: bool, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "success": success,
        "contentItems": [
            {"type": "inputText", "text": json.dumps(payload, ensure_ascii=False)}
        ],
    }


def sync_text_for_agent(text_id: str) -> dict[str, Any]:
    """Persist the browser's current text before delivering a user turn."""
    result = TEXT_BROKER.call(text_id, "sync_snapshot")
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "text_sync_failed")
    payload = result.get("payload")
    if not isinstance(payload, dict) or type(payload.get("revision")) is not int:
        raise RuntimeError("text_invalid_response")
    return payload


def _offline_read(text_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    manifest, content = load_text_snapshot(text_id)
    editor_kind = text_editor_kind(manifest)
    result_scope, result_text = scope_text_snapshot(
        content=content,
        arguments=arguments,
        editor_kind=editor_kind,
        max_chars=TEXT_READ_MAX_CHARS,
    )
    return {
        "text_id": text_id,
        "editor_kind": editor_kind,
        "format": manifest["format"],
        "revision": int(manifest.get("current_revision") or 0),
        "scope": result_scope,
        "text": result_text,
        "live": False,
    }


def text_dynamic_tool_handler_for_text(text_id: str) -> Callable[[dict], dict]:
    def handler(call: dict) -> dict:
        namespace = str(call.get("namespace") or "")
        tool = str(call.get("tool") or "")
        arguments = call.get("arguments")
        if namespace != "text" or tool not in {"read", "apply_patch", "export"}:
            return _content_result(False, {"error": "unsupported_text_tool"})
        if not isinstance(arguments, dict):
            return _content_result(False, {"error": "invalid_arguments"})
        manifest = read_text_manifest(text_id)
        if not manifest:
            return _content_result(False, {"error": "text_not_found"})
        try:
            if tool == "export":
                try:
                    live_result = TEXT_BROKER.call(text_id, "export_snapshot")
                except (RuntimeError, TimeoutError):
                    live_result = None
                if isinstance(live_result, dict):
                    if not live_result.get("ok"):
                        return _content_result(
                            False,
                            {"error": live_result.get("error") or "text_call_failed"},
                        )
                return _content_result(True, save_text_export(text_id))
            if tool == "read":
                try:
                    result = TEXT_BROKER.call(text_id, "read", arguments)
                except (RuntimeError, TimeoutError):
                    return _content_result(True, _offline_read(text_id, arguments))
            else:
                if arguments.get("base_revision") != int(manifest.get("current_revision") or 0):
                    return _content_result(
                        False,
                        {"error": "revision_conflict", "revision": int(manifest.get("current_revision") or 0)},
                    )
                result = TEXT_BROKER.call(text_id, "apply_patch", arguments)
            payload = result.get("payload") if isinstance(result, dict) else None
            if not result.get("ok"):
                return _content_result(False, payload or {"error": result.get("error") or "text_call_failed"})
            return _content_result(
                True,
                {
                    **(payload or {}),
                    **text_file_references(text_id),
                },
            )
        except Exception as exc:
            return _content_result(False, {"error": str(exc)})

    return handler


def text_dynamic_tool_handler(thread_id: str) -> Callable[[dict], dict]:
    manifest = text_manifest_for_thread(thread_id)
    text_id = str((manifest or {}).get("text_id") or "")
    return text_dynamic_tool_handler_for_text(text_id)
