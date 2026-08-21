from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


CANVAS_ROOT = Path(".nomad_surface") / "canvases"
CANVAS_ID_PATTERN = re.compile(r"^canvas-[0-9a-f]{24}$")
CANVAS_REVISION_LIMIT = 40
CANVAS_SCHEMA_VERSION = 3
CANVAS_COMMAND_RECEIPT_SCHEMA_VERSION = 1
CANVAS_COMMAND_RECEIPT_MAX_CHANGED_IDS = 500
CANVAS_COMMAND_RECEIPT_MAX_REFS = 500
CANVAS_COMMAND_RECEIPT_MAX_WARNINGS = 100
CANVAS_PREVIEW_IMAGE_MIME_TYPE = "image/webp"
CANVAS_PREVIEW_IMAGE_MIME_TYPES = frozenset(
    {"image/webp", "image/jpeg", "image/png"}
)
CANVAS_VISUAL_PREVIEW_PATH = "current/preview.webp"
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class CanvasRevisionConflict(RuntimeError):
    def __init__(self, current_revision: int) -> None:
        super().__init__(f"Canvas revision is now {current_revision}.")
        self.current_revision = current_revision


class CanvasManifestVersionError(RuntimeError):
    def __init__(self, schema_version: int) -> None:
        super().__init__(
            "Canvas manifest schema version "
            f"{schema_version} is newer than supported version {CANVAS_SCHEMA_VERSION}."
        )
        self.schema_version = schema_version


class CanvasCommandReceiptError(RuntimeError):
    pass


def canvas_id_for_thread(thread_id: str) -> str:
    digest = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()[:24]
    return f"canvas-{digest}"


def _validate_canvas_id(canvas_id: str) -> str:
    if not CANVAS_ID_PATTERN.fullmatch(canvas_id):
        raise ValueError("Invalid canvas ID.")
    return canvas_id


def canvas_directory(canvas_id: str) -> Path:
    return CANVAS_ROOT / _validate_canvas_id(canvas_id)


def _canvas_lock(canvas_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(canvas_id, threading.Lock())


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _manifest_path(canvas_id: str) -> Path:
    return canvas_directory(canvas_id) / "manifest.json"


def _command_receipt_path(canvas_id: str, command_id: str) -> Path:
    digest = hashlib.sha256(command_id.encode("utf-8")).hexdigest()
    return canvas_directory(canvas_id) / "commands" / f"{digest}.json"


def _validate_command_receipt(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CanvasCommandReceiptError("Canvas command receipt is not an object.")
    required = {
        "schema_version",
        "command_id",
        "input_hash",
        "base_revision",
        "result_revision",
        "changed_ids",
        "refs",
        "warnings",
        "committed_at",
    }
    if set(value) != required:
        raise CanvasCommandReceiptError("Canvas command receipt fields are invalid.")
    if value.get("schema_version") != CANVAS_COMMAND_RECEIPT_SCHEMA_VERSION:
        raise CanvasCommandReceiptError("Canvas command receipt version is unsupported.")
    command_id = value.get("command_id")
    input_hash = value.get("input_hash")
    changed_ids = value.get("changed_ids")
    refs = value.get("refs")
    warnings = value.get("warnings")
    if not isinstance(command_id, str) or not 1 <= len(command_id) <= 128:
        raise CanvasCommandReceiptError("Canvas command receipt command ID is invalid.")
    if not isinstance(input_hash, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", input_hash):
        raise CanvasCommandReceiptError("Canvas command receipt input hash is invalid.")
    if type(value.get("base_revision")) is not int or value["base_revision"] < 0:
        raise CanvasCommandReceiptError("Canvas command receipt base revision is invalid.")
    if type(value.get("result_revision")) is not int or value["result_revision"] < 0:
        raise CanvasCommandReceiptError("Canvas command receipt result revision is invalid.")
    if (
        not isinstance(changed_ids, list)
        or len(changed_ids) > CANVAS_COMMAND_RECEIPT_MAX_CHANGED_IDS
        or any(not isinstance(item, str) or len(item) > 256 for item in changed_ids)
    ):
        raise CanvasCommandReceiptError("Canvas command receipt changed IDs are invalid.")
    if (
        not isinstance(refs, dict)
        or len(refs) > CANVAS_COMMAND_RECEIPT_MAX_REFS
        or any(
            not isinstance(key, str)
            or not isinstance(item, str)
            or len(key) > 128
            or len(item) > 256
            for key, item in refs.items()
        )
    ):
        raise CanvasCommandReceiptError("Canvas command receipt refs are invalid.")
    if (
        not isinstance(warnings, list)
        or len(warnings) > CANVAS_COMMAND_RECEIPT_MAX_WARNINGS
        or any(not isinstance(item, str) or len(item) > 500 for item in warnings)
    ):
        raise CanvasCommandReceiptError("Canvas command receipt warnings are invalid.")
    if not isinstance(value.get("committed_at"), str) or len(value["committed_at"]) > 64:
        raise CanvasCommandReceiptError("Canvas command receipt timestamp is invalid.")
    return value


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise CanvasCommandReceiptError("Canvas command receipt could not be read.") from exc
    if not isinstance(value, dict):
        raise CanvasCommandReceiptError("Canvas command receipt is not an object.")
    return value


def _materialize_command_receipt(path: Path, receipt: dict[str, Any]) -> None:
    """Best-effort cache for receipts whose committed metadata is authoritative."""
    try:
        _atomic_write(path, _json_bytes(receipt))
    except OSError:
        pass


def read_canvas_command_receipt(
    canvas_id: str, command_id: str
) -> dict[str, Any] | None:
    if not isinstance(command_id, str) or not 1 <= len(command_id) <= 128:
        raise CanvasCommandReceiptError("Canvas command ID is invalid.")
    with _canvas_lock(canvas_id):
        manifest = read_canvas_manifest(canvas_id)
        if not manifest:
            raise FileNotFoundError("Canvas manifest was not found.")
        current_revision = int(manifest.get("current_revision") or 0)
        receipt_path = _command_receipt_path(canvas_id, command_id)
        raw = _read_json_object(receipt_path)
        if raw is not None:
            receipt = _validate_command_receipt(raw)
            if receipt["command_id"] != command_id:
                raise CanvasCommandReceiptError("Canvas command receipt ID does not match.")
            if receipt["result_revision"] > current_revision:
                raise CanvasCommandReceiptError(
                    "Canvas command receipt references an uncommitted revision."
                )
            return receipt

        revisions = canvas_directory(canvas_id) / "revisions"
        lower_bound = max(1, current_revision - CANVAS_REVISION_LIMIT + 1)
        for revision in range(current_revision, lower_bound - 1, -1):
            metadata = _read_json_object(
                revisions / f"{revision:08d}" / "metadata.json"
            )
            if not metadata:
                continue
            candidate = metadata.get("command_receipt")
            if not (
                isinstance(candidate, dict)
                and candidate.get("command_id") == command_id
            ):
                candidate = None
            if candidate is None:
                continue
            receipt = _validate_command_receipt(candidate)
            if receipt["result_revision"] != revision:
                raise CanvasCommandReceiptError(
                    "Canvas command receipt revision metadata does not match."
                )
            _materialize_command_receipt(receipt_path, receipt)
            return receipt
        return None


def read_canvas_manifest(canvas_id: str) -> dict[str, Any] | None:
    path = _manifest_path(canvas_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return raw if isinstance(raw, dict) else None


def _supported_manifest_version(manifest: dict[str, Any]) -> int:
    schema_version = int(manifest.get("schema_version") or 1)
    if schema_version > CANVAS_SCHEMA_VERSION:
        raise CanvasManifestVersionError(schema_version)
    return schema_version


def _initialize_canvas_manifest(
    canvas_id: str,
    *,
    thread_id: str = "",
    draft_id: str = "",
    project_path: str = "",
) -> dict[str, Any]:
    with _canvas_lock(canvas_id):
        existing = read_canvas_manifest(canvas_id)
        if existing:
            schema_version = _supported_manifest_version(existing)
            changed = False
            if (
                schema_version < CANVAS_SCHEMA_VERSION
                or existing.get("visual_preview") != CANVAS_VISUAL_PREVIEW_PATH
            ):
                existing["schema_version"] = CANVAS_SCHEMA_VERSION
                existing["visual_preview"] = CANVAS_VISUAL_PREVIEW_PATH
                changed = True
            if project_path and not existing.get("project_path"):
                existing["project_path"] = project_path
                changed = True
            if thread_id and not existing.get("thread_id"):
                existing["thread_id"] = thread_id
                changed = True
            if draft_id and not existing.get("draft_id"):
                existing["draft_id"] = draft_id
                changed = True
            if changed:
                _atomic_write(_manifest_path(canvas_id), _json_bytes(existing))
            return existing
        directory = canvas_directory(canvas_id)
        directory.mkdir(parents=True, exist_ok=True)
        created_at = _timestamp()
        manifest = {
            "schema_version": CANVAS_SCHEMA_VERSION,
            "canvas_id": canvas_id,
            "thread_id": thread_id,
            "draft_id": draft_id,
            "project_path": project_path,
            "current_revision": 0,
            "document": "current/document.json",
            "preview": "current/preview.svg",
            "visual_preview": CANVAS_VISUAL_PREVIEW_PATH,
            "visual_preview_mime_type": CANVAS_PREVIEW_IMAGE_MIME_TYPE,
            "content_hash": "",
            "created_at": created_at,
            "updated_at": created_at,
        }
        _atomic_write(_manifest_path(canvas_id), _json_bytes(manifest))
        return manifest


def initialize_canvas(thread_id: str, project_path: str = "") -> dict[str, Any]:
    canvas_id = canvas_id_for_thread(thread_id)
    return _initialize_canvas_manifest(
        canvas_id,
        thread_id=thread_id,
        project_path=project_path,
    )


def initialize_canvas_draft(draft_id: str, project_path: str = "") -> dict[str, Any]:
    canvas_id = canvas_id_for_thread(draft_id)
    return _initialize_canvas_manifest(
        canvas_id,
        draft_id=draft_id,
        project_path=project_path,
    )


def bind_canvas_to_thread(canvas_id: str, thread_id: str) -> dict[str, Any]:
    if not thread_id:
        raise ValueError("A thread ID is required.")
    with _canvas_lock(canvas_id):
        manifest = read_canvas_manifest(canvas_id)
        if not manifest:
            raise FileNotFoundError("Canvas manifest was not found.")
        schema_version = _supported_manifest_version(manifest)
        if manifest.get("thread_id") == thread_id:
            return manifest
        manifest = {
            **manifest,
            "schema_version": max(schema_version, CANVAS_SCHEMA_VERSION),
            "thread_id": thread_id,
            "visual_preview": CANVAS_VISUAL_PREVIEW_PATH,
            "updated_at": _timestamp(),
        }
        _atomic_write(_manifest_path(canvas_id), _json_bytes(manifest))
        return manifest


def canvas_manifest_for_thread(thread_id: str) -> dict[str, Any] | None:
    if not thread_id:
        return None
    direct = read_canvas_manifest(canvas_id_for_thread(thread_id))
    if direct and direct.get("thread_id") == thread_id:
        return direct
    return next(
        (
            manifest
            for manifest in list_canvas_manifests()
            if manifest.get("thread_id") == thread_id
        ),
        None,
    )


def canvas_exists_for_thread(thread_id: str) -> bool:
    return canvas_manifest_for_thread(thread_id) is not None


def canvas_exists(canvas_id: str) -> bool:
    try:
        return read_canvas_manifest(canvas_id) is not None
    except ValueError:
        return False


def list_canvas_manifests() -> list[dict[str, Any]]:
    try:
        directories = list(CANVAS_ROOT.iterdir())
    except OSError:
        return []
    manifests = []
    for directory in directories:
        if not directory.is_dir() or not CANVAS_ID_PATTERN.fullmatch(directory.name):
            continue
        manifest = read_canvas_manifest(directory.name)
        if manifest:
            manifests.append(manifest)
    return sorted(
        manifests,
        key=lambda item: str(item.get("updated_at") or ""),
        reverse=True,
    )


def load_canvas_document(canvas_id: str) -> dict[str, Any] | None:
    manifest = read_canvas_manifest(canvas_id)
    if not manifest:
        return None
    relative_path = Path(str(manifest.get("document") or "current/document.json"))
    if relative_path.is_absolute() or ".." in relative_path.parts:
        return None
    path = canvas_directory(canvas_id) / relative_path
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def load_canvas_preview(canvas_id: str) -> str:
    path = canvas_directory(canvas_id) / "current" / "preview.svg"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def load_canvas_visual_preview(canvas_id: str) -> bytes:
    path = canvas_directory(canvas_id) / CANVAS_VISUAL_PREVIEW_PATH
    try:
        return path.read_bytes()
    except OSError:
        return b""


def canvas_visual_preview_data_url(canvas_id: str) -> str:
    preview = load_canvas_visual_preview(canvas_id)
    if not preview:
        return ""
    manifest = read_canvas_manifest(canvas_id) or {}
    mime_type = str(
        manifest.get("visual_preview_mime_type") or CANVAS_PREVIEW_IMAGE_MIME_TYPE
    ).lower()
    if mime_type not in CANVAS_PREVIEW_IMAGE_MIME_TYPES:
        mime_type = CANVAS_PREVIEW_IMAGE_MIME_TYPE
    encoded = base64.b64encode(preview).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _prune_revisions(directory: Path) -> None:
    revisions = sorted(
        (item for item in directory.iterdir() if item.is_dir() and item.name.isdigit()),
        key=lambda item: int(item.name),
    )
    for revision in revisions[:-CANVAS_REVISION_LIMIT]:
        for child in revision.iterdir():
            if child.is_file():
                child.unlink()
        revision.rmdir()


def _materialize_current_snapshot(
    document_path: Path,
    document_bytes: bytes,
    preview_path: Path,
    preview_bytes: bytes,
    visual_preview_path: Path,
    visual_preview_bytes: bytes,
) -> None:
    """Best-effort compatibility cache written only after the manifest commit."""
    for path, content in (
        (document_path, document_bytes),
        (preview_path, preview_bytes),
        (visual_preview_path, visual_preview_bytes),
    ):
        try:
            _atomic_write(path, content)
        except OSError:
            pass


def save_canvas_snapshot(
    canvas_id: str,
    document: dict[str, Any],
    preview_svg: str = "",
    preview_image: bytes | None = b"",
    preview_image_mime_type: str = CANVAS_PREVIEW_IMAGE_MIME_TYPE,
    *,
    expected_revision: int | None = None,
    command_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ValueError("Canvas document must be a JSON object.")
    preview_image_mime_type = str(preview_image_mime_type).lower()
    if preview_image_mime_type not in CANVAS_PREVIEW_IMAGE_MIME_TYPES:
        raise ValueError("Canvas preview image type is not supported.")
    document_bytes = _json_bytes(document)
    content_hash = hashlib.sha256(document_bytes).hexdigest()

    with _canvas_lock(canvas_id):
        manifest = read_canvas_manifest(canvas_id)
        if not manifest:
            raise FileNotFoundError("Canvas manifest was not found.")
        schema_version = _supported_manifest_version(manifest)
        manifest_needs_update = (
            schema_version < CANVAS_SCHEMA_VERSION
            or manifest.get("visual_preview") != CANVAS_VISUAL_PREVIEW_PATH
            or manifest.get("visual_preview_mime_type")
            not in CANVAS_PREVIEW_IMAGE_MIME_TYPES
        )
        if manifest_needs_update:
            manifest = {
                **manifest,
                "schema_version": CANVAS_SCHEMA_VERSION,
                "visual_preview": CANVAS_VISUAL_PREVIEW_PATH,
                "visual_preview_mime_type": CANVAS_PREVIEW_IMAGE_MIME_TYPE,
            }
        current_revision = int(manifest.get("current_revision") or 0)
        if expected_revision is not None and expected_revision != current_revision:
            raise CanvasRevisionConflict(current_revision)

        receipt_base: dict[str, Any] | None = None
        if command_receipt is not None:
            receipt_base = {
                "schema_version": CANVAS_COMMAND_RECEIPT_SCHEMA_VERSION,
                "command_id": command_receipt.get("command_id"),
                "input_hash": command_receipt.get("input_hash"),
                "base_revision": command_receipt.get("base_revision"),
                "result_revision": current_revision,
                "changed_ids": command_receipt.get("changed_ids", []),
                "refs": command_receipt.get("refs", {}),
                "warnings": command_receipt.get("warnings", []),
                "committed_at": _timestamp(),
            }

        directory = canvas_directory(canvas_id)
        current_document = directory / "current" / "document.json"
        current_preview = directory / "current" / "preview.svg"
        current_visual_preview = directory / CANVAS_VISUAL_PREVIEW_PATH
        preview_mime_changed = (
            preview_image is not None
            and manifest.get("visual_preview_mime_type") != preview_image_mime_type
        )
        if manifest.get("content_hash") == content_hash and receipt_base is None:
            if preview_svg != load_canvas_preview(canvas_id):
                _atomic_write(current_preview, preview_svg.encode("utf-8"))
            if (
                preview_image is not None
                and preview_image != load_canvas_visual_preview(canvas_id)
            ):
                _atomic_write(current_visual_preview, preview_image)
            if preview_image is not None:
                manifest["visual_preview_mime_type"] = preview_image_mime_type
            if manifest_needs_update or preview_mime_changed:
                _atomic_write(_manifest_path(canvas_id), _json_bytes(manifest))
            return manifest

        revision = current_revision + 1
        revision_directory = directory / "revisions" / f"{revision:08d}"
        # Reuse an uncommitted directory left by an interrupted previous save.
        # The manifest remains the authority for the current revision.
        revision_directory.mkdir(parents=True, exist_ok=True)
        _atomic_write(revision_directory / "document.json", document_bytes)
        revision_metadata: dict[str, Any] = {
            "revision": revision,
            "created_at": _timestamp(),
        }
        if receipt_base is not None:
            receipt_base["result_revision"] = revision
            receipt_base["committed_at"] = _timestamp()
            revision_metadata["command_receipt"] = _validate_command_receipt(
                receipt_base
            )
        _atomic_write(
            revision_directory / "metadata.json", _json_bytes(revision_metadata)
        )

        manifest = {
            **manifest,
            "current_revision": revision,
            "document": f"revisions/{revision:08d}/document.json",
            "content_hash": content_hash,
            "updated_at": _timestamp(),
            "visual_preview_mime_type": preview_image_mime_type,
        }
        _atomic_write(_manifest_path(canvas_id), _json_bytes(manifest))
        _materialize_current_snapshot(
            current_document,
            document_bytes,
            current_preview,
            preview_svg.encode("utf-8"),
            current_visual_preview,
            preview_image or b"",
        )
        if receipt_base is not None:
            receipt = _validate_command_receipt(receipt_base)
            _materialize_command_receipt(
                _command_receipt_path(canvas_id, receipt["command_id"]), receipt
            )
        try:
            _prune_revisions(directory / "revisions")
        except OSError:
            # Retention cleanup happens after the manifest commit and must not turn a
            # durable command into a negative acknowledgement.
            pass
        return manifest


def canvas_file_references(canvas_id: str) -> dict[str, str]:
    directory = canvas_directory(canvas_id).resolve()
    manifest = read_canvas_manifest(canvas_id) or {}
    document_path = Path(
        str(manifest.get("document") or "current/document.json")
    )
    if document_path.is_absolute() or ".." in document_path.parts:
        document_path = Path("current/document.json")
    return {
        "document_path": str(directory / document_path),
        "preview_path": str(directory / "current" / "preview.svg"),
        "visual_preview_path": str(directory / CANVAS_VISUAL_PREVIEW_PATH),
    }


def scene_from_document(document: dict[str, Any] | None) -> dict[str, Any]:
    if not document:
        return {"shapes": [], "bindings": [], "pages": []}
    store = document.get("store")
    records = store.values() if isinstance(store, dict) else []
    shapes = []
    bindings = []
    pages = []
    for record in records:
        if not isinstance(record, dict):
            continue
        record_type = str(record.get("typeName") or "")
        if record_type == "shape":
            shapes.append(record)
        elif record_type == "binding":
            bindings.append(record)
        elif record_type == "page":
            pages.append(record)
    return {"shapes": shapes, "bindings": bindings, "pages": pages}
