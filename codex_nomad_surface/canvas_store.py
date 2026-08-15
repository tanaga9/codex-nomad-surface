from __future__ import annotations

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
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class CanvasRevisionConflict(RuntimeError):
    def __init__(self, current_revision: int) -> None:
        super().__init__(f"Canvas revision is now {current_revision}.")
        self.current_revision = current_revision


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


def read_canvas_manifest(canvas_id: str) -> dict[str, Any] | None:
    path = _manifest_path(canvas_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return raw if isinstance(raw, dict) else None


def initialize_canvas(thread_id: str, project_path: str = "") -> dict[str, Any]:
    canvas_id = canvas_id_for_thread(thread_id)
    with _canvas_lock(canvas_id):
        existing = read_canvas_manifest(canvas_id)
        if existing:
            if project_path and not existing.get("project_path"):
                existing["project_path"] = project_path
                _atomic_write(_manifest_path(canvas_id), _json_bytes(existing))
            return existing
        directory = canvas_directory(canvas_id)
        directory.mkdir(parents=True, exist_ok=True)
        created_at = _timestamp()
        manifest = {
            "schema_version": 1,
            "canvas_id": canvas_id,
            "thread_id": thread_id,
            "project_path": project_path,
            "current_revision": 0,
            "document": "current/document.json",
            "preview": "current/preview.svg",
            "content_hash": "",
            "created_at": created_at,
            "updated_at": created_at,
        }
        _atomic_write(_manifest_path(canvas_id), _json_bytes(manifest))
        return manifest


def canvas_exists_for_thread(thread_id: str) -> bool:
    manifest = read_canvas_manifest(canvas_id_for_thread(thread_id))
    return bool(manifest and manifest.get("thread_id") == thread_id)


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
    path = canvas_directory(canvas_id) / "current" / "document.json"
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


def save_canvas_snapshot(
    canvas_id: str,
    document: dict[str, Any],
    preview_svg: str = "",
    *,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ValueError("Canvas document must be a JSON object.")
    document_bytes = _json_bytes(document)
    content_hash = hashlib.sha256(document_bytes).hexdigest()

    with _canvas_lock(canvas_id):
        manifest = read_canvas_manifest(canvas_id)
        if not manifest:
            raise FileNotFoundError("Canvas manifest was not found.")
        current_revision = int(manifest.get("current_revision") or 0)
        if expected_revision is not None and expected_revision != current_revision:
            raise CanvasRevisionConflict(current_revision)

        directory = canvas_directory(canvas_id)
        current_document = directory / "current" / "document.json"
        current_preview = directory / "current" / "preview.svg"
        if manifest.get("content_hash") == content_hash:
            if preview_svg != load_canvas_preview(canvas_id):
                _atomic_write(current_preview, preview_svg.encode("utf-8"))
            return manifest

        revision = current_revision + 1
        revision_directory = directory / "revisions" / f"{revision:08d}"
        # Reuse an uncommitted directory left by an interrupted previous save.
        # The manifest remains the authority for the current revision.
        revision_directory.mkdir(parents=True, exist_ok=True)
        _atomic_write(revision_directory / "document.json", document_bytes)
        _atomic_write(
            revision_directory / "metadata.json",
            _json_bytes({"revision": revision, "created_at": _timestamp()}),
        )
        _atomic_write(current_document, document_bytes)
        _atomic_write(current_preview, preview_svg.encode("utf-8"))

        manifest = {
            **manifest,
            "current_revision": revision,
            "content_hash": content_hash,
            "updated_at": _timestamp(),
        }
        _atomic_write(_manifest_path(canvas_id), _json_bytes(manifest))
        _prune_revisions(directory / "revisions")
        return manifest


def canvas_file_references(canvas_id: str) -> dict[str, str]:
    directory = canvas_directory(canvas_id).resolve()
    return {
        "document_path": str(directory / "current" / "document.json"),
        "preview_path": str(directory / "current" / "preview.svg"),
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
