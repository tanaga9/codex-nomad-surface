from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import queue
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from starlette.websockets import WebSocket, WebSocketDisconnect

from codex_nomad_surface.canvas_store import (
    CANVAS_COMMAND_RECEIPT_MAX_CHANGED_IDS,
    CanvasCommandReceiptError,
    canvas_exists,
    canvas_file_references,
    canvas_id_for_thread,
    canvas_visual_preview_data_url,
    load_canvas_document,
    read_canvas_manifest,
    read_canvas_command_receipt,
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
CANVAS_PREVIEW_IMAGE_PREFIXES = {
    "data:image/webp;base64,": "image/webp",
    "data:image/jpeg;base64,": "image/jpeg",
    "data:image/png;base64,": "image/png",
}
_CANVAS_APPLY_LOCKS: dict[str, threading.Lock] = {}
_CANVAS_APPLY_LOCKS_GUARD = threading.Lock()
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


def _canvas_apply_lock(canvas_id: str) -> threading.Lock:
    with _CANVAS_APPLY_LOCKS_GUARD:
        return _CANVAS_APPLY_LOCKS.setdefault(canvas_id, threading.Lock())


def _canvas_command_input_hash(arguments: dict[str, Any]) -> str:
    canonical = json.dumps(
        arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _receipt_payload(canvas_id: str, receipt: dict[str, Any]) -> dict[str, Any]:
    return {
        "live": True,
        "revision": receipt["result_revision"],
        "changed_ids": receipt["changed_ids"],
        "refs": receipt["refs"],
        "warnings": receipt["warnings"],
        "replayed": True,
        **canvas_file_references(canvas_id),
    }


def _canvas_command_status(
    canvas_id: str, arguments: object
) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        return {"ok": False, "error": "invalid_command_status"}
    command_id = arguments.get("command_id")
    if not isinstance(command_id, str) or not 1 <= len(command_id) <= 128:
        return {"ok": False, "error": "invalid_command_status"}
    input_hash = _canvas_command_input_hash(arguments)
    with _canvas_apply_lock(canvas_id):
        try:
            receipt = read_canvas_command_receipt(canvas_id, command_id)
        except (CanvasCommandReceiptError, FileNotFoundError) as exc:
            return {"ok": False, "error": f"command_status_failed: {exc}"}
        if receipt is None:
            return {"ok": False, "error": "command_not_committed"}
        if receipt["input_hash"] != input_hash:
            return {"ok": False, "error": "command_id_conflict"}
        return {"ok": True, "error": ""}


def _decode_preview_image(data_url: object) -> tuple[bytes, str]:
    value = str(data_url or "")
    if not value:
        return b"", "image/webp"
    prefix, mime_type = next(
        (
            (candidate_prefix, candidate_mime_type)
            for candidate_prefix, candidate_mime_type in CANVAS_PREVIEW_IMAGE_PREFIXES.items()
            if value.startswith(candidate_prefix)
        ),
        ("", ""),
    )
    if not prefix:
        raise ValueError("Canvas preview must be a WebP, JPEG, or PNG data URL.")
    encoded = value[len(prefix) :]
    if len(encoded) > (CANVAS_PREVIEW_IMAGE_MAX_BYTES * 4 // 3) + 4:
        raise ValueError("Canvas preview is too large.")
    try:
        preview = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Canvas preview is not valid base64.") from exc
    if len(preview) > CANVAS_PREVIEW_IMAGE_MAX_BYTES:
        raise ValueError("Canvas preview is too large.")
    return preview, mime_type


def _save_canvas_payload(
    canvas_id: str,
    document: dict[str, Any],
    preview_svg: str,
    preview_image_url: object,
    preview_image_error: object = "",
    command_receipt: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    preview_error = str(preview_image_error or "")
    if not preview_image_url and preview_svg:
        preview_image = None
        preview_image_mime_type = "image/webp"
        preview_error = preview_error or "Canvas preview image is unavailable."
    else:
        try:
            preview_image, preview_image_mime_type = _decode_preview_image(
                preview_image_url
            )
        except ValueError as exc:
            preview_image = None
            preview_image_mime_type = "image/webp"
            preview_error = str(exc)
    manifest = save_canvas_snapshot(
        canvas_id,
        document,
        preview_svg,
        preview_image,
        preview_image_mime_type,
        expected_revision=(
            int(command_receipt["base_revision"])
            if command_receipt is not None
            else None
        ),
        command_receipt=command_receipt,
    )
    return manifest, preview_error


@dataclass
class PendingCanvasRequest:
    event: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None
    context: dict[str, Any] = field(default_factory=dict)


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
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        pending = PendingCanvasRequest(context=context or {})
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

    def request_context(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            pending = self._pending.get(request_id)
            return dict(pending.context) if pending else None


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
            if message_type == "command_status":
                status = await asyncio.to_thread(
                    _canvas_command_status, canvas_id, message.get("arguments")
                )
                connection.outgoing.put(
                    {
                        "type": "response_ack",
                        "id": str(message.get("id") or ""),
                        **status,
                    }
                )
                continue
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
            request_context = CANVAS_BROKER.request_context(request_id)
            if request_context is None:
                continue
            result: dict[str, Any] = {
                "ok": bool(message.get("ok")),
                "error": str(message.get("error") or ""),
            }
            is_apply_response = bool(request_context.get("command_id"))
            if result["ok"] and is_apply_response and (
                not isinstance(payload, dict)
                or not isinstance(payload.get("document"), dict)
            ):
                result = {
                    "ok": False,
                    "error": "canvas_invalid_response: apply result has no document",
                }
                payload = {
                    "error": "canvas_invalid_response",
                    "message": "A successful apply response must include a document.",
                }
            if isinstance(payload, dict):
                document = payload.pop("document", None)
                preview_svg = str(payload.pop("preview_svg", "") or "")
                preview_image_url = str(
                    payload.pop("preview_image_url", "") or ""
                )
                if bool(message.get("ok")) and isinstance(document, dict):
                    try:
                        command_receipt = (
                            {
                                **request_context,
                                "changed_ids": payload.get("changed_ids", []),
                                "refs": payload.get("refs", {}),
                                "warnings": payload.get("warnings", []),
                            }
                            if request_context.get("command_id")
                            else None
                        )
                        manifest, preview_error = await asyncio.to_thread(
                            _save_canvas_payload,
                            canvas_id,
                            document,
                            preview_svg,
                            preview_image_url,
                            payload.get("preview_image_error"),
                            command_receipt,
                        )
                        payload["revision"] = int(
                            manifest.get("current_revision") or 0
                        )
                        if preview_error:
                            preview_image_url = ""
                            payload["preview_image_error"] = preview_error
                    except Exception as exc:
                        result = {"ok": False, "error": f"save_failed: {exc}"}
                        payload = {"error": "save_failed", "message": str(exc)}
                if result["ok"]:
                    payload.update(canvas_file_references(canvas_id))
                    if preview_image_url:
                        payload["preview_image_url"] = preview_image_url
                if is_apply_response and result["ok"]:
                    payload = {
                        "live": True,
                        "revision": payload.get("revision", 0),
                        "changed_ids": payload.get("changed_ids", []),
                        "refs": payload.get("refs", {}),
                        "warnings": payload.get("warnings", []),
                        **canvas_file_references(canvas_id),
                    }
                result["payload"] = payload
            if is_apply_response:
                connection.outgoing.put(
                    {
                        "type": "response_ack",
                        "id": request_id,
                        "ok": result["ok"],
                        "error": result.get("error", ""),
                    }
                )
            CANVAS_BROKER.resolve(request_id, result)
    except WebSocketDisconnect:
        pass
    finally:
        CANVAS_BROKER.unregister(canvas_id, connection)
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


def _canvas_apply_patch_schema() -> dict[str, Any]:
    coordinate = {"type": "number", "minimum": -1_000_000, "maximum": 1_000_000}
    dimension = {"type": "number", "exclusiveMinimum": 0, "maximum": 1_000_000}
    text = {"type": "string", "maxLength": 20_000}
    target = {
        "oneOf": [
            _closed_object({"id": {"type": "string", "minLength": 1, "maxLength": 256}}, ["id"]),
            _closed_object({"ref": {"type": "string", "minLength": 1, "maxLength": 128}}, ["ref"]),
        ]
    }
    common_style = {
        "color": {
            "type": "string",
            "enum": ["black", "grey", "light-violet", "violet", "blue", "light-blue", "yellow", "orange", "green", "light-green", "light-red", "red", "white"],
        },
        "size": {"type": "string", "enum": ["s", "m", "l", "xl"]},
        "font": {"type": "string", "enum": ["draw", "sans", "serif", "mono"]},
    }
    geo_style = _closed_object(
        {
            **common_style,
            "geo": {"type": "string", "enum": ["rectangle", "ellipse", "triangle", "diamond", "pentagon", "hexagon", "octagon", "star", "rhombus", "rhombus-2", "oval", "trapezoid", "arrow-right", "arrow-left", "arrow-up", "arrow-down", "x-box", "check-box", "cloud", "heart"]},
            "fill": {"type": "string", "enum": ["none", "semi", "solid", "pattern", "fill", "lined-fill"]},
            "dash": {"type": "string", "enum": ["draw", "solid", "dashed", "dotted", "none"]},
            "align": {"type": "string", "enum": ["start", "middle", "end"]},
            "vertical_align": {"type": "string", "enum": ["start", "middle", "end"]},
        }
    )
    text_style = _closed_object(
        {
            **common_style,
            "align": {"type": "string", "enum": ["start", "middle", "end"]},
        }
    )
    note_style = _closed_object(
        {
            **common_style,
            "align": {"type": "string", "enum": ["start", "middle", "end"]},
            "vertical_align": {"type": "string", "enum": ["start", "middle", "end"]},
        }
    )
    frame_style = _closed_object({"color": common_style["color"]})
    shape_common = {"x": coordinate, "y": coordinate}
    create_shapes = [
        _closed_object(
            {"type": {"const": "geo"}, **shape_common, "width": dimension, "height": dimension, "text": text, "style": geo_style},
            ["type", "x", "y"],
        ),
        _closed_object(
            {"type": {"const": "text"}, **shape_common, "width": dimension, "text": text, "style": text_style},
            ["type", "x", "y", "text"],
        ),
        _closed_object(
            {"type": {"const": "note"}, **shape_common, "width": dimension, "height": dimension, "text": text, "style": note_style},
            ["type", "x", "y", "width", "height"],
        ),
        _closed_object(
            {"type": {"const": "frame"}, **shape_common, "width": dimension, "height": dimension, "name": {"type": "string", "maxLength": 500}, "style": frame_style},
            ["type", "x", "y"],
        ),
    ]
    update_style = _closed_object(
        {
            **common_style,
            "geo": geo_style["properties"]["geo"],
            "fill": geo_style["properties"]["fill"],
            "dash": geo_style["properties"]["dash"],
            "align": geo_style["properties"]["align"],
            "vertical_align": geo_style["properties"]["vertical_align"],
        }
    )
    update_style["minProperties"] = 1
    arrow_style = _closed_object(
        {
            "color": common_style["color"],
            "size": common_style["size"],
            "dash": geo_style["properties"]["dash"],
            "arrowhead_start": {"type": "string", "enum": ["none", "arrow", "triangle", "square", "dot", "diamond", "pipe", "inverted", "bar"]},
            "arrowhead_end": {"type": "string", "enum": ["none", "arrow", "triangle", "square", "dot", "diamond", "pipe", "inverted", "bar"]},
        }
    )
    update_operation = _closed_object(
        {
            "op": {"const": "update"},
            "target": target,
            "x": coordinate,
            "y": coordinate,
            "width": dimension,
            "height": dimension,
            "text": text,
            "name": {"type": "string", "maxLength": 500},
            "style": update_style,
        },
        ["op", "target"],
    )
    update_operation["anyOf"] = [
        {"required": [field]}
        for field in ["x", "y", "width", "height", "text", "name", "style"]
    ]
    operations = [
        _closed_object(
            {"op": {"const": "create"}, "ref": {"type": "string", "minLength": 1, "maxLength": 128}, "shape": {"oneOf": create_shapes}},
            ["op", "ref", "shape"],
        ),
        update_operation,
        _closed_object(
            {"op": {"const": "move"}, "target": target, "x": coordinate, "y": coordinate},
            ["op", "target", "x", "y"],
        ),
        _closed_object(
            {"op": {"const": "resize"}, "target": target, "width": dimension, "height": dimension},
            ["op", "target", "width", "height"],
        ),
        _closed_object(
            {"op": {"const": "delete"}, "targets": {"type": "array", "minItems": 1, "maxItems": 100, "items": target}},
            ["op", "targets"],
        ),
        _closed_object(
            {"op": {"const": "connect"}, "ref": {"type": "string", "minLength": 1, "maxLength": 128}, "from": target, "to": target, "text": text, "style": arrow_style},
            ["op", "ref", "from", "to"],
        ),
    ]
    return _closed_object(
        {
            "command_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "base_revision": {"type": "integer", "minimum": 0},
            "operations": {"type": "array", "minItems": 1, "maxItems": 100, "items": {"oneOf": operations}},
        },
        ["command_id", "base_revision", "operations"],
    )


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
                        "delete, or connect operations to the live canvas. Use "
                        "Nomad fields such as shape.text, width, and height; raw "
                        "tldraw props are not accepted. Targets contain exactly one "
                        "id or an earlier ref from the same ordered batch. Read the "
                        "scene first and use its revision as base_revision. A command "
                        "ID is idempotent and cannot be reused with different input."
                        " A patch may change at most "
                        f"{CANVAS_COMMAND_RECEIPT_MAX_CHANGED_IDS} unique shapes."
                    ),
                    "inputSchema": _canvas_apply_patch_schema(),
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
        request_context: dict[str, Any] | None = None
        if tool == "apply_patch":
            command_id = arguments.get("command_id")
            base_revision = arguments.get("base_revision")
            operations = arguments.get("operations")
            if (
                not isinstance(command_id, str)
                or not 1 <= len(command_id) <= 128
                or not isinstance(base_revision, int)
                or isinstance(base_revision, bool)
                or base_revision < 0
                or not isinstance(operations, list)
                or not 1 <= len(operations) <= 100
            ):
                return _content_result(
                    False,
                    {
                        "error": "patch_validation_failed",
                        "operation_errors": [
                            {
                                "operation_index": -1,
                                "path": "request",
                                "code": "invalid_operation",
                                "message": "The patch request does not satisfy the Canvas contract.",
                            }
                        ],
                    },
                )
            input_hash = _canvas_command_input_hash(arguments)
            with _canvas_apply_lock(canvas_id):
                try:
                    receipt = read_canvas_command_receipt(canvas_id, command_id)
                except CanvasCommandReceiptError as exc:
                    return _content_result(
                        False,
                        {"error": "receipt_corrupt", "message": str(exc)},
                    )
                if receipt:
                    if receipt["input_hash"] != input_hash:
                        return _content_result(
                            False,
                            {
                                "error": "command_id_conflict",
                                "result_revision": receipt["result_revision"],
                            },
                        )
                    return _content_result(True, _receipt_payload(canvas_id, receipt))

                current_manifest = read_canvas_manifest(canvas_id)
                if not current_manifest:
                    return _content_result(False, {"error": "canvas_not_found"})
                revision = int(current_manifest.get("current_revision") or 0)
                if base_revision != revision:
                    return _content_result(
                        False,
                        {"error": "revision_conflict", "current_revision": revision},
                    )
                request_context = {
                    "command_id": command_id,
                    "input_hash": input_hash,
                    "base_revision": base_revision,
                }

                try:
                    broker_result = CANVAS_BROKER.call(
                        canvas_id, tool, arguments, context=request_context
                    )
                except RuntimeError as exc:
                    return _content_result(False, {"error": str(exc)})
                except TimeoutError as exc:
                    return _content_result(False, {"error": str(exc)})
        else:
            try:
                broker_result = CANVAS_BROKER.call(canvas_id, tool, arguments)
            except RuntimeError as exc:
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
