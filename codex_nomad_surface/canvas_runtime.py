from __future__ import annotations

import asyncio
import base64
import binascii
import json
import queue
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from starlette.websockets import WebSocket, WebSocketDisconnect

from codex_nomad_surface.canvas_store import (
    canvas_exists,
    canvas_file_references,
    canvas_id_for_thread,
    canvas_visual_preview_data_url,
    load_canvas_document,
    read_canvas_manifest,
    save_canvas_snapshot,
    scene_from_document,
)
from codex_nomad_surface.http_gate import (
    auth_cookie_from_scope,
    auth_required,
    valid_auth_session_token,
)


CANVAS_TOOL_TIMEOUT_SECONDS = 25.0
CANVAS_REPLACED_CLOSE_CODE = 4001
CANVAS_PREVIEW_IMAGE_MAX_BYTES = 8 * 1024 * 1024
CANVAS_PREVIEW_IMAGE_PREFIX = "data:image/png;base64,"
CANVAS_DEVELOPER_INSTRUCTIONS = (
    "This thread uses the Nomad Surface embedded Canvas. When a request concerns "
    "the canvas, use the canvas dynamic tools as the primary interface. Read the "
    "current scene before scene-dependent edits. A non-empty read_scene result "
    "normally includes a whole-canvas image; interpret it together with the "
    "structured shape and binding data so freehand marks are understood as a "
    "composition, not only as isolated objects. If the result reports that the "
    "visual preview is unavailable, state that limitation rather than guessing. "
    "Then apply edits through "
    "canvas.apply_patch using the returned revision. Treat backing document and "
    "preview files as persistence artifacts, not as the canvas interface. Do not "
    "inspect or modify those files, and do not use external or offline canvas "
    "integrations, unless the user explicitly requests it. If the canvas tools do "
    "not provide enough information, explain the limitation instead of silently "
    "switching interfaces."
)


def _decode_preview_image(data_url: object) -> bytes:
    value = str(data_url or "")
    if not value:
        return b""
    if not value.startswith(CANVAS_PREVIEW_IMAGE_PREFIX):
        raise ValueError("Canvas preview must be a PNG data URL.")
    encoded = value[len(CANVAS_PREVIEW_IMAGE_PREFIX) :]
    if len(encoded) > (CANVAS_PREVIEW_IMAGE_MAX_BYTES * 4 // 3) + 4:
        raise ValueError("Canvas preview is too large.")
    try:
        preview = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Canvas preview is not valid base64.") from exc
    if len(preview) > CANVAS_PREVIEW_IMAGE_MAX_BYTES:
        raise ValueError("Canvas preview is too large.")
    return preview


def _save_canvas_payload(
    canvas_id: str,
    document: dict[str, Any],
    preview_svg: str,
    preview_image_url: object,
    preview_image_error: object = "",
) -> tuple[dict[str, Any], str]:
    preview_error = str(preview_image_error or "")
    if not preview_image_url and preview_svg:
        preview_image = None
        preview_error = preview_error or "Canvas preview image is unavailable."
    else:
        try:
            preview_image = _decode_preview_image(preview_image_url)
        except ValueError as exc:
            preview_image = None
            preview_error = str(exc)
    manifest = save_canvas_snapshot(
        canvas_id,
        document,
        preview_svg,
        preview_image,
    )
    return manifest, preview_error


@dataclass
class PendingCanvasRequest:
    event: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None


@dataclass
class CanvasConnection:
    outgoing: queue.Queue[dict[str, Any] | None] = field(default_factory=queue.Queue)
    retired: threading.Event = field(default_factory=threading.Event)


class CanvasBroker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._connections: dict[str, CanvasConnection] = {}
        self._pending: dict[str, PendingCanvasRequest] = {}

    def register(self, canvas_id: str) -> CanvasConnection:
        connection = CanvasConnection()
        with self._lock:
            previous = self._connections.pop(canvas_id, None)
            if previous:
                previous.retired.set()
                previous.outgoing.put(None)
            self._connections[canvas_id] = connection
        return connection

    def is_current(self, canvas_id: str, connection: CanvasConnection) -> bool:
        with self._lock:
            return self._connections.get(canvas_id) is connection

    def unregister(self, canvas_id: str, connection: CanvasConnection) -> None:
        with self._lock:
            if self._connections.get(canvas_id) is connection:
                self._connections.pop(canvas_id, None)
        connection.outgoing.put(None)

    def call(
        self,
        canvas_id: str,
        method: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        pending = PendingCanvasRequest()
        with self._lock:
            connection = self._connections.get(canvas_id)
            if not connection:
                raise RuntimeError("canvas_unavailable")
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
        if not pending.event.wait(CANVAS_TOOL_TIMEOUT_SECONDS):
            with self._lock:
                self._pending.pop(request_id, None)
            raise TimeoutError("canvas_timeout")
        return pending.result or {"ok": False, "error": "canvas_no_result"}

    def resolve(self, request_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            pending = self._pending.pop(request_id, None)
        if pending:
            pending.result = result
            pending.event.set()


CANVAS_BROKER = CanvasBroker()


async def _canvas_sender(websocket: WebSocket, connection: CanvasConnection) -> None:
    while True:
        message = await asyncio.to_thread(connection.outgoing.get)
        if message is None:
            if connection.retired.is_set():
                try:
                    await websocket.close(code=CANVAS_REPLACED_CLOSE_CODE)
                except RuntimeError:
                    pass
            return
        await websocket.send_json(message)


async def canvas_websocket(websocket: WebSocket) -> None:
    if auth_required() and not valid_auth_session_token(
        auth_cookie_from_scope(websocket.scope)
    ):
        await websocket.close(code=4401)
        return

    canvas_id = str(websocket.path_params.get("canvas_id") or "")
    if not canvas_exists(canvas_id):
        await websocket.close(code=4404)
        return

    await websocket.accept()
    connection = CANVAS_BROKER.register(canvas_id)
    sender = asyncio.create_task(_canvas_sender(websocket, connection))
    try:
        while True:
            message = await websocket.receive_json()
            if not CANVAS_BROKER.is_current(canvas_id, connection):
                return
            if not isinstance(message, dict):
                continue
            message_type = str(message.get("type") or "")
            if message_type == "snapshot":
                document = message.get("document")
                if isinstance(document, dict):
                    await asyncio.to_thread(
                        _save_canvas_payload,
                        canvas_id,
                        document,
                        str(message.get("preview_svg") or ""),
                        message.get("preview_image_url"),
                        message.get("preview_image_error"),
                    )
                continue
            if message_type != "response":
                continue

            request_id = str(message.get("id") or "")
            payload = message.get("payload")
            result: dict[str, Any] = {
                "ok": bool(message.get("ok")),
                "error": str(message.get("error") or ""),
            }
            if isinstance(payload, dict):
                document = payload.pop("document", None)
                preview_svg = str(payload.pop("preview_svg", "") or "")
                preview_image_url = str(
                    payload.pop("preview_image_url", "") or ""
                )
                if isinstance(document, dict):
                    try:
                        manifest, preview_error = await asyncio.to_thread(
                            _save_canvas_payload,
                            canvas_id,
                            document,
                            preview_svg,
                            preview_image_url,
                            payload.get("preview_image_error"),
                        )
                        payload["revision"] = int(
                            manifest.get("current_revision") or 0
                        )
                        if preview_error:
                            preview_image_url = ""
                            payload["preview_image_error"] = preview_error
                    except Exception as exc:
                        result = {"ok": False, "error": f"save_failed: {exc}"}
                payload.update(canvas_file_references(canvas_id))
                if preview_image_url:
                    payload["preview_image_url"] = preview_image_url
                result["payload"] = payload
            CANVAS_BROKER.resolve(request_id, result)
    except WebSocketDisconnect:
        pass
    finally:
        CANVAS_BROKER.unregister(canvas_id, connection)
        sender.cancel()


def canvas_dynamic_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "namespace",
            "name": "canvas",
            "description": (
                "Read and edit the live Nomad Surface embedded Canvas for this "
                "thread. This is separate from external or offline canvas apps."
            ),
            "tools": [
                {
                    "type": "function",
                    "name": "read_scene",
                    "description": (
                        "Read the current canvas before editing it. Returns shapes, "
                        "their page-space bounds when available, bindings, page "
                        "information, the current revision, and a whole-canvas image "
                        "for visual interpretation when the canvas is non-empty."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                },
                {
                    "type": "function",
                    "name": "apply_patch",
                    "description": (
                        "Apply a bounded batch of create, update, move, resize, "
                        "delete, or connect operations to the live canvas. Read the "
                        "scene first and use its revision as base_revision."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "required": ["command_id", "base_revision", "operations"],
                        "properties": {
                            "command_id": {"type": "string"},
                            "base_revision": {"type": "integer", "minimum": 0},
                            "operations": {
                                "type": "array",
                                "maxItems": 100,
                                "items": {
                                    "type": "object",
                                    "required": ["op"],
                                    "properties": {
                                        "op": {
                                            "type": "string",
                                            "enum": [
                                                "create",
                                                "update",
                                                "move",
                                                "resize",
                                                "delete",
                                                "connect",
                                            ],
                                        }
                                    },
                                    "additionalProperties": True,
                                },
                            },
                        },
                        "additionalProperties": False,
                    },
                },
            ],
        }
    ]


def canvas_initial_context_items() -> list[dict[str, Any]]:
    """Return the developer context injected once when a Canvas thread starts."""
    return [
        {
            "type": "message",
            "role": "developer",
            "content": [
                {
                    "type": "input_text",
                    "text": CANVAS_DEVELOPER_INSTRUCTIONS,
                }
            ],
        }
    ]


def _content_result(
    success: bool,
    value: dict[str, Any],
    *,
    image_url: str = "",
) -> dict[str, Any]:
    content_items: list[dict[str, str]] = [
        {
            "type": "inputText",
            "text": json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        }
    ]
    if image_url:
        content_items.append({"type": "inputImage", "imageUrl": image_url})
    return {
        "success": success,
        "contentItems": content_items,
    }


def canvas_dynamic_tool_handler_for_canvas(
    canvas_id: str,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def handle(params: dict[str, Any]) -> dict[str, Any]:
        namespace = str(params.get("namespace") or "")
        tool = str(params.get("tool") or "")
        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        if namespace != "canvas" or tool not in {"read_scene", "apply_patch"}:
            return _content_result(False, {"error": "unsupported_canvas_tool"})

        manifest = read_canvas_manifest(canvas_id)
        if not manifest:
            return _content_result(False, {"error": "canvas_not_found"})
        revision = int(manifest.get("current_revision") or 0)
        if tool == "apply_patch":
            base_revision = int(arguments.get("base_revision") or 0)
            if base_revision != revision:
                return _content_result(
                    False,
                    {"error": "revision_conflict", "current_revision": revision},
                )

        try:
            broker_result = CANVAS_BROKER.call(canvas_id, tool, arguments)
        except RuntimeError as exc:
            if tool == "read_scene":
                document = load_canvas_document(canvas_id)
                image_url = canvas_visual_preview_data_url(canvas_id)
                return _content_result(
                    True,
                    {
                        "live": False,
                        "revision": revision,
                        "scene": scene_from_document(document),
                        "visual_preview_available": bool(image_url),
                        **canvas_file_references(canvas_id),
                    },
                    image_url=image_url,
                )
            return _content_result(False, {"error": str(exc)})
        except TimeoutError as exc:
            return _content_result(False, {"error": str(exc)})

        success = bool(broker_result.get("ok"))
        payload = broker_result.get("payload")
        if not isinstance(payload, dict):
            payload = {"error": broker_result.get("error") or "canvas_call_failed"}
        image_url = str(payload.pop("preview_image_url", "") or "")
        payload.setdefault("live", True)
        return _content_result(
            success,
            payload,
            image_url=image_url if success and tool == "read_scene" else "",
        )

    return handle


def canvas_dynamic_tool_handler(
    thread_id: str,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    return canvas_dynamic_tool_handler_for_canvas(canvas_id_for_thread(thread_id))
