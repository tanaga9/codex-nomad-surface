from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import queue
import re
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
CANVAS_READ_MAX_REQUESTED_IDS = 100
CANVAS_READ_MAX_SHAPES = 500
CANVAS_READ_MAX_BINDINGS = 1_000
CANVAS_READ_MAX_BOUND_DIMENSION = 1_000_000
CANVAS_READ_MIN_IMAGE_DIMENSION = 64
CANVAS_READ_MAX_IMAGE_DIMENSION = 2048
CANVAS_READ_MAX_TEXT_LENGTH = 2_000
CANVAS_READ_MAX_FULL_VALUE_LENGTH = 20_000
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
    "current scene before scene-dependent edits. Prefer the narrowest useful "
    "read_scene scope. When requested, a non-empty result may include a scoped "
    "image; interpret it together with the "
    "structured shape and binding data so freehand marks are understood as a "
    "composition, not only as isolated objects. If the result reports that the "
    "visual preview is unavailable, state that limitation rather than guessing. "
    "Then apply edits through "
    "canvas.apply_patch using the returned revision. "
    "Use document-unique semantic_id values for domain-significant shapes and "
    "semantic connectors, and use source_refs for bounded provenance locators; "
    "do not place source passages in metadata or dereference source_refs through "
    "the Canvas tool. Prefer semantic_id targets for later domain edits and exact "
    "tldraw IDs for decoration. Treat structured lint errors as incomplete "
    "semantic success and address them before claiming the diagram is complete. "
    "Treat backing document and "
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
    semantic_success = not any(
        isinstance(item, dict) and item.get("severity") == "error"
        for item in receipt["warnings"]
    )
    return {
        "live": True,
        "revision": receipt["result_revision"],
        "changed_ids": receipt["changed_ids"],
        "refs": receipt["refs"],
        "warnings": receipt["warnings"],
        "semantic_success": semantic_success,
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
                        "semantic_success": payload.get("semantic_success", True),
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
    semantic_id = {
        "type": "string",
        "minLength": 1,
        "maxLength": 128,
        "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
    }
    source_ref = _closed_object(
        {
            "document": {"type": "string", "minLength": 1, "maxLength": 500},
            "locator": {"type": "string", "minLength": 1, "maxLength": 500},
            "label": {"type": "string", "maxLength": 500},
            "content_hash": {
                "type": "string",
                "maxLength": 128,
                "pattern": r"^sha256:[0-9a-f]{64}$",
            },
        },
        ["document", "locator"],
    )
    source_refs = {
        "type": "array",
        "maxItems": 20,
        "items": source_ref,
    }
    target = {
        "oneOf": [
            _closed_object({"id": {"type": "string", "minLength": 1, "maxLength": 256}}, ["id"]),
            _closed_object({"semantic_id": semantic_id}, ["semantic_id"]),
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
    shape_common = {
        "x": coordinate,
        "y": coordinate,
        "semantic_id": semantic_id,
        "source_refs": source_refs,
    }
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
            "semantic_id": {"oneOf": [semantic_id, {"type": "null"}]},
            "source_refs": source_refs,
        },
        ["op", "target"],
    )
    update_operation["anyOf"] = [
        {"required": [field]}
        for field in ["x", "y", "width", "height", "text", "name", "style", "semantic_id", "source_refs"]
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
            {"op": {"const": "connect"}, "ref": {"type": "string", "minLength": 1, "maxLength": 128}, "from": target, "to": target, "text": text, "style": arrow_style, "semantic_id": semantic_id, "source_refs": source_refs},
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


def _canvas_read_scene_schema() -> dict[str, Any]:
    coordinate = {
        "type": "number",
        "minimum": -CANVAS_READ_MAX_BOUND_DIMENSION,
        "maximum": CANVAS_READ_MAX_BOUND_DIMENSION,
    }
    dimension = {
        "type": "number",
        "exclusiveMinimum": 0,
        "maximum": CANVAS_READ_MAX_BOUND_DIMENSION,
    }
    scopes = [
        _closed_object({"type": {"const": scope_type}}, ["type"])
        for scope_type in ("all", "viewport", "selection")
    ]
    scopes.extend(
        [
            _closed_object(
                {
                    "type": {"const": "bounds"},
                    "x": coordinate,
                    "y": coordinate,
                    "width": dimension,
                    "height": dimension,
                },
                ["type", "x", "y", "width", "height"],
            ),
            _closed_object(
                {
                    "type": {"const": "frame"},
                    "id": {"type": "string", "minLength": 1, "maxLength": 256},
                },
                ["type", "id"],
            ),
            _closed_object(
                {
                    "type": {"const": "shape_ids"},
                    "ids": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": CANVAS_READ_MAX_REQUESTED_IDS,
                        "uniqueItems": True,
                        "items": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 256,
                        },
                    },
                    "include_descendants": {"type": "boolean"},
                },
                ["type", "ids"],
            ),
        ]
    )
    return _closed_object(
        {
            "scope": {"oneOf": scopes},
            "detail": {"type": "string", "enum": ["compact", "standard", "full"]},
            "include_image": {"type": "boolean"},
            "max_image_dimension": {
                "type": "integer",
                "minimum": CANVAS_READ_MIN_IMAGE_DIMENSION,
                "maximum": CANVAS_READ_MAX_IMAGE_DIMENSION,
            },
        }
    )


def _normalize_canvas_read_arguments(arguments: object) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise ValueError("Canvas read arguments must be an object.")
    if set(arguments) - {"scope", "detail", "include_image", "max_image_dimension"}:
        raise ValueError("Canvas read arguments contain unsupported fields.")
    detail = arguments.get("detail", "compact")
    include_image = arguments.get("include_image", True)
    max_image_dimension = arguments.get("max_image_dimension", 1536)
    if detail not in {"compact", "standard", "full"}:
        raise ValueError("Canvas read detail is invalid.")
    if type(include_image) is not bool:
        raise ValueError("Canvas read include_image must be a boolean.")
    if (
        type(max_image_dimension) is not int
        or not CANVAS_READ_MIN_IMAGE_DIMENSION
        <= max_image_dimension
        <= CANVAS_READ_MAX_IMAGE_DIMENSION
    ):
        raise ValueError("Canvas read image dimension is invalid.")
    scope = arguments.get("scope", {"type": "all"})
    if not isinstance(scope, dict) or not isinstance(scope.get("type"), str):
        raise ValueError("Canvas read scope is invalid.")
    scope_type = scope["type"]
    if scope_type in {"all", "viewport", "selection"}:
        if set(scope) != {"type"}:
            raise ValueError("Canvas read scope fields are invalid.")
        normalized_scope = {"type": scope_type}
    elif scope_type == "bounds":
        if set(scope) != {"type", "x", "y", "width", "height"}:
            raise ValueError("Canvas bounds scope fields are invalid.")
        values = [scope.get(key) for key in ("x", "y", "width", "height")]
        if any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not -CANVAS_READ_MAX_BOUND_DIMENSION
            <= value
            <= CANVAS_READ_MAX_BOUND_DIMENSION
            for value in values
        ) or scope["width"] <= 0 or scope["height"] <= 0:
            raise ValueError("Canvas bounds scope values are invalid.")
        normalized_scope = dict(scope)
    elif scope_type == "frame":
        frame_id = scope.get("id")
        if (
            set(scope) != {"type", "id"}
            or not isinstance(frame_id, str)
            or not 1 <= len(frame_id) <= 256
        ):
            raise ValueError("Canvas frame scope is invalid.")
        normalized_scope = dict(scope)
    elif scope_type == "shape_ids":
        ids = scope.get("ids")
        include_descendants = scope.get("include_descendants", True)
        if (
            set(scope) - {"type", "ids", "include_descendants"}
            or not isinstance(ids, list)
            or not 1 <= len(ids) <= CANVAS_READ_MAX_REQUESTED_IDS
            or len(set(ids)) != len(ids)
            or any(not isinstance(item, str) or not 1 <= len(item) <= 256 for item in ids)
            or type(include_descendants) is not bool
        ):
            raise ValueError("Canvas shape_ids scope is invalid.")
        normalized_scope = {
            "type": "shape_ids",
            "ids": ids,
            "include_descendants": include_descendants,
        }
    else:
        raise ValueError("Canvas read scope type is unsupported.")
    return {
        "scope": normalized_scope,
        "detail": detail,
        "include_image": include_image,
        "max_image_dimension": max_image_dimension,
    }


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
                        "Read a bounded scope of the current canvas before editing. "
                        "Supports all, viewport, selection, bounds, frame, and "
                        "shape_ids scopes with compact, standard, or full detail. "
                        "Images are returned separately and may be omitted."
                    ),
                    "inputSchema": _canvas_read_scene_schema(),
                },
                {
                    "type": "function",
                    "name": "apply_patch",
                    "description": (
                        "Apply a bounded batch of create, update, move, resize, "
                        "delete, or connect operations to the live canvas. Use "
                        "Nomad fields such as shape.text, width, and height; raw "
                        "tldraw props are not accepted. Read the scene first and use "
                        "its revision as base_revision. A command "
                        "ID is idempotent and cannot be reused with different input. "
                        "Targets accept exactly one id, semantic_id, or earlier ref. "
                        "Use semantic_id and bounded source_refs for domain objects; "
                        "omitting source_refs preserves existing provenance."
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


def _bounded_canvas_value(value: Any, maximum: int = CANVAS_READ_MAX_FULL_VALUE_LENGTH) -> Any:
    try:
        serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return {"truncated": True}
    if len(serialized) <= maximum:
        return value
    return {"truncated": True, "preview": serialized[:maximum]}


def _canvas_text_summary(value: Any) -> str:
    parts: list[str] = []

    def visit(item: Any) -> None:
        if sum(len(part) for part in parts) >= CANVAS_READ_MAX_TEXT_LENGTH:
            return
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, dict):
            if isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item.get("content"), list):
                visit(item["content"])

    visit(value)
    summary = " ".join(" ".join(parts).split())
    if len(summary) > CANVAS_READ_MAX_TEXT_LENGTH:
        return summary[:CANVAS_READ_MAX_TEXT_LENGTH] + "…"
    return summary


def _offline_semantic_summary(meta: dict[str, Any]) -> dict[str, Any]:
    nomad = meta.get("nomad") if isinstance(meta.get("nomad"), dict) else {}
    summary: dict[str, Any] = {}
    semantic_id = nomad.get("semantic_id")
    if isinstance(semantic_id, str) and re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", semantic_id
    ):
        summary["semantic_id"] = semantic_id
    source_refs = nomad.get("source_refs")
    if isinstance(source_refs, list):
        bounded_refs = []
        for item in source_refs[:20]:
            if not isinstance(item, dict) or set(item) - {
                "document",
                "locator",
                "label",
                "content_hash",
            }:
                continue
            document = item.get("document")
            locator = item.get("locator")
            label = item.get("label")
            content_hash = item.get("content_hash")
            if (
                not isinstance(document, str)
                or not 1 <= len(document) <= 500
                or not isinstance(locator, str)
                or not 1 <= len(locator) <= 500
                or (
                    label is not None
                    and (not isinstance(label, str) or len(label) > 500)
                )
                or (
                    content_hash is not None
                    and (
                        not isinstance(content_hash, str)
                        or not re.fullmatch(r"sha256:[0-9a-f]{64}", content_hash)
                    )
                )
            ):
                continue
            bounded_refs.append(item)
        summary["source_refs"] = bounded_refs
    created_by = nomad.get("created_by")
    if isinstance(created_by, str) and len(created_by) <= 64:
        summary["created_by"] = created_by
    last_command_id = nomad.get("last_command_id")
    if isinstance(last_command_id, str) and len(last_command_id) <= 128:
        summary["last_command_id"] = last_command_id
    return summary


def _offline_shape_bounds(
    shape: dict[str, Any], page_ids: set[str]
) -> dict[str, float] | None:
    x = shape.get("x")
    y = shape.get("y")
    props = shape.get("props")
    rotation = shape.get("rotation", 0)
    if (
        shape.get("parentId") not in page_ids
        or not isinstance(x, (int, float))
        or isinstance(x, bool)
        or not isinstance(y, (int, float))
        or isinstance(y, bool)
        or not isinstance(rotation, (int, float))
        or isinstance(rotation, bool)
        or rotation != 0
    ):
        return None
    if not isinstance(props, dict):
        props = {}
    scale = props.get("scale", 1)
    if not isinstance(scale, (int, float)) or isinstance(scale, bool) or scale != 1:
        return None
    width = props.get("w")
    height = props.get("h")
    if shape.get("type") == "arrow":
        start = props.get("start")
        end = props.get("end")
        if isinstance(start, dict) and isinstance(end, dict):
            coordinates = [start.get("x"), start.get("y"), end.get("x"), end.get("y")]
            if all(
                isinstance(item, (int, float)) and not isinstance(item, bool)
                for item in coordinates
            ):
                sx, sy, ex, ey = coordinates
                return {
                    "x": float(x + min(sx, ex)),
                    "y": float(y + min(sy, ey)),
                    "width": float(abs(ex - sx)),
                    "height": float(abs(ey - sy)),
                }
    if (
        not isinstance(width, (int, float))
        or isinstance(width, bool)
        or not isinstance(height, (int, float))
        or isinstance(height, bool)
    ):
        return None
    return {
        "x": float(x),
        "y": float(y),
        "width": float(width),
        "height": float(height),
    }


def _offline_shape_result(
    shape: dict[str, Any], detail: str, page_ids: set[str]
) -> dict[str, Any]:
    props = shape.get("props") if isinstance(shape.get("props"), dict) else {}
    meta = shape.get("meta") if isinstance(shape.get("meta"), dict) else {}
    text = _canvas_text_summary(
        props.get("richText") or props.get("text") or props.get("name") or ""
    )
    bounds = _offline_shape_bounds(shape, page_ids)
    result: dict[str, Any] = {
        "id": shape.get("id"),
        "type": shape.get("type"),
        "parent_id": shape.get("parentId"),
        "index": shape.get("index"),
    }
    if bounds:
        result["page_bounds"] = bounds
    if text:
        result["text"] = text
    semantic = _offline_semantic_summary(meta)
    if semantic:
        result["semantic"] = semantic
    if detail in {"standard", "full"}:
        result.update(
            {
                "x": shape.get("x"),
                "y": shape.get("y"),
                "rotation": shape.get("rotation"),
            }
        )
        allowed = {
            "w",
            "h",
            "geo",
            "color",
            "fill",
            "dash",
            "size",
            "font",
            "align",
            "verticalAlign",
            "autoSize",
            "start",
            "end",
            "arrowheadStart",
            "arrowheadEnd",
            "name",
        }
        result["props"] = {key: value for key, value in props.items() if key in allowed}
    if detail == "full":
        result["props"] = _bounded_canvas_value(props)
        result["meta"] = _bounded_canvas_value(meta)
    return result


def _offline_shape_page_id(
    shape: dict[str, Any],
    shapes_by_id: dict[str, dict[str, Any]],
    page_ids: set[str],
) -> str | None:
    parent_id = shape.get("parentId")
    visited: set[str] = set()
    while isinstance(parent_id, str) and parent_id not in visited:
        if parent_id in page_ids:
            return parent_id
        visited.add(parent_id)
        parent = shapes_by_id.get(parent_id)
        if parent is None:
            return None
        parent_id = parent.get("parentId")
    return None


def _offline_canvas_read(
    canvas_id: str,
    document: dict[str, Any] | None,
    request: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    scope = request["scope"]
    if scope["type"] in {"viewport", "selection"}:
        raise ValueError("scope_requires_live_editor")
    scene = scene_from_document(document)
    shapes = [item for item in scene["shapes"] if isinstance(item.get("id"), str)]
    shapes_by_id = {item["id"]: item for item in shapes}
    page_ids = {
        page["id"]
        for page in scene["pages"]
        if isinstance(page.get("id"), str)
    }
    if scope["type"] in {"all", "bounds"} and len(page_ids) > 1:
        raise ValueError("scope_requires_live_editor")
    offline_warnings: list[str] = []
    selected_ids: set[str]
    if scope["type"] == "all":
        selected_ids = set(shapes_by_id)
    elif scope["type"] == "frame":
        frame = shapes_by_id.get(scope["id"])
        if not frame or frame.get("type") != "frame":
            raise LookupError("scope_not_found")
        selected_ids = {scope["id"]}
    elif scope["type"] == "shape_ids":
        if any(shape_id not in shapes_by_id for shape_id in scope["ids"]):
            raise LookupError("scope_not_found")
        selected_ids = set(scope["ids"])
    else:
        bounds = scope
        selected_ids = set()
        for shape in shapes:
            shape_bounds = _offline_shape_bounds(shape, page_ids)
            if not shape_bounds:
                raise ValueError("scope_requires_live_editor")
            if not (
                shape_bounds["x"] + shape_bounds["width"] < bounds["x"]
                or bounds["x"] + bounds["width"] < shape_bounds["x"]
                or shape_bounds["y"] + shape_bounds["height"] < bounds["y"]
                or bounds["y"] + bounds["height"] < shape_bounds["y"]
            ):
                selected_ids.add(shape["id"])
    include_descendants = scope["type"] == "frame" or (
        scope["type"] == "shape_ids" and scope["include_descendants"]
    )
    if include_descendants:
        changed = True
        while changed:
            changed = False
            for shape in shapes:
                if shape.get("parentId") in selected_ids and shape["id"] not in selected_ids:
                    selected_ids.add(shape["id"])
                    changed = True
    selected_page_ids = {
        page_id
        for shape_id in selected_ids
        if (
            page_id := _offline_shape_page_id(
                shapes_by_id[shape_id], shapes_by_id, page_ids
            )
        )
    }
    if len(selected_page_ids) > 1 or (
        selected_ids and len(selected_page_ids) != 1
    ):
        raise ValueError("scope_requires_live_editor")
    ordered = sorted(
        (shape for shape in shapes if shape["id"] in selected_ids),
        key=lambda item: str(item.get("index") or ""),
    )
    total_shapes = len(ordered)
    returned = ordered[:CANVAS_READ_MAX_SHAPES]
    returned_ids = {shape["id"] for shape in returned}
    unresolved_returned_bounds = sum(
        _offline_shape_bounds(shape, page_ids) is None for shape in returned
    )
    if unresolved_returned_bounds:
        offline_warnings.append(
            f"{unresolved_returned_bounds} saved shapes require the live editor for page bounds."
        )
    bindings = []
    for binding in scene["bindings"]:
        from_id = binding.get("fromId")
        to_id = binding.get("toId")
        if from_id not in returned_ids and to_id not in returned_ids:
            continue
        binding_result = {
            "id": binding.get("id"),
            "type": binding.get("type"),
            "from": {
                "id": from_id,
                "external_to_scope": from_id not in returned_ids,
            },
            "to": {
                "id": to_id,
                "external_to_scope": to_id not in returned_ids,
            },
        }
        binding_props = (
            binding.get("props") if isinstance(binding.get("props"), dict) else {}
        )
        if request["detail"] == "standard":
            allowed_binding_props = {
                "terminal",
                "normalizedAnchor",
                "isPrecise",
                "isExact",
                "snap",
            }
            binding_result["props"] = {
                key: value
                for key, value in binding_props.items()
                if key in allowed_binding_props
            }
        elif request["detail"] == "full":
            binding_result["props"] = _bounded_canvas_value(binding_props)
        bindings.append(binding_result)
    total_bindings = len(bindings)
    bindings = bindings[:CANVAS_READ_MAX_BINDINGS]
    truncated = total_shapes > len(returned) or total_bindings > len(bindings)
    page_id = next(iter(selected_page_ids or page_ids), "")
    image_url = ""
    image_available = False
    image_error = ""
    if request["include_image"]:
        if scope["type"] == "all":
            image_url = canvas_visual_preview_data_url(canvas_id)
            image_available = bool(image_url)
        else:
            image_error = "scoped_preview_requires_live_editor"
    result = {
        "page_id": page_id,
        "scope": scope,
        "detail": request["detail"],
        "truncated": truncated,
        "total_shapes": total_shapes,
        "returned_shapes": len(returned),
        "total_bindings": total_bindings,
        "returned_bindings": len(bindings),
        **(
            {"suggested_scope": "Use frame, viewport, bounds, or shape_ids."}
            if truncated
            else {}
        ),
        "shapes": [
            _offline_shape_result(shape, request["detail"], page_ids)
            for shape in returned
        ],
        "bindings": bindings,
        "warnings": offline_warnings,
        "image": {
            "included": image_available,
            "width": 0,
            "height": 0,
            "scope": scope,
            **({"error": image_error} if image_error else {}),
        },
    }
    return result, image_url


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
                arguments = _normalize_canvas_read_arguments(arguments)
            except ValueError as exc:
                return _content_result(
                    False,
                    {"error": "read_validation_failed", "message": str(exc)},
                )
            try:
                broker_result = CANVAS_BROKER.call(canvas_id, tool, arguments)
            except RuntimeError as exc:
                document = load_canvas_document(canvas_id)
                try:
                    offline_scene, image_url = _offline_canvas_read(
                        canvas_id, document, arguments
                    )
                except ValueError as read_error:
                    if str(read_error) == "scope_requires_live_editor":
                        return _content_result(
                            False,
                            {
                                "error": "scope_requires_live_editor",
                                "scope": arguments["scope"],
                            },
                        )
                    raise
                except LookupError:
                    return _content_result(
                        False,
                        {"error": "scope_not_found", "scope": arguments["scope"]},
                    )
                return _content_result(
                    True,
                    {
                        "live": False,
                        "revision": revision,
                        **offline_scene,
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
        if tool == "read_scene":
            payload.setdefault("revision", revision)
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
