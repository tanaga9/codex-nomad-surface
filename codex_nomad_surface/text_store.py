from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal


TEXT_ROOT = Path(".nomad_surface") / "texts"
TEXT_ID_PATTERN = re.compile(r"^text-[0-9a-f]{24}$")
TEXT_SCHEMA_VERSION = 1
TEXT_REVISION_LIMIT = 20
TEXT_FORMATS = frozenset({"plain", "markdown"})
TEXT_PRESENTATIONS = frozenset({"raw", "assisted"})
TEXT_EDITOR_KINDS = frozenset({"text", "document"})
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class TextRevisionConflict(RuntimeError):
    def __init__(self, current_revision: int) -> None:
        super().__init__(f"Text revision is now {current_revision}.")
        self.current_revision = current_revision


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _text_lock(text_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(text_id, threading.Lock())


def _validate_text_id(text_id: str) -> str:
    if not TEXT_ID_PATTERN.fullmatch(text_id):
        raise ValueError("Invalid text ID.")
    return text_id


def _text_id(seed: str) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    return f"text-{digest}"


def text_directory(text_id: str) -> Path:
    return TEXT_ROOT / _validate_text_id(text_id)


def _manifest_path(text_id: str) -> Path:
    return text_directory(text_id) / "manifest.json"


def _extension(text_format: str) -> str:
    return "md" if text_format == "markdown" else "txt"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _write_json(path: Path, value: dict[str, Any]) -> None:
    _atomic_write(
        path,
        json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8") + b"\n",
    )


def read_text_manifest(text_id: str) -> dict[str, Any] | None:
    try:
        value = json.loads(_manifest_path(text_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    value["editor_kind"] = text_editor_kind(value)
    return value


def text_editor_kind(manifest: dict[str, Any]) -> str:
    editor_kind = str(manifest.get("editor_kind") or "")
    if editor_kind in TEXT_EDITOR_KINDS:
        return editor_kind
    return "document" if manifest.get("format") == "markdown" else "text"


def list_text_manifests() -> list[dict[str, Any]]:
    if not TEXT_ROOT.is_dir():
        return []
    manifests = []
    for directory in TEXT_ROOT.iterdir():
        if not directory.is_dir() or not TEXT_ID_PATTERN.fullmatch(directory.name):
            continue
        manifest = read_text_manifest(directory.name)
        if manifest:
            manifests.append(manifest)
    return sorted(manifests, key=lambda item: str(item.get("updated_at") or ""), reverse=True)


def text_manifest_for_thread(thread_id: str) -> dict[str, Any] | None:
    return next(
        (
            manifest
            for manifest in list_text_manifests()
            if str(manifest.get("thread_id") or "") == thread_id
        ),
        None,
    )


def text_exists_for_thread(thread_id: str) -> bool:
    return text_manifest_for_thread(thread_id) is not None


def text_exists(text_id: str) -> bool:
    return read_text_manifest(text_id) is not None


def initialize_text(
    thread_id: str,
    project_path: str = "",
    *,
    text_format: Literal["plain", "markdown"] = "markdown",
    presentation: Literal["raw", "assisted"] = "assisted",
    editor_kind: Literal["text", "document"] | None = None,
) -> dict[str, Any]:
    existing = text_manifest_for_thread(thread_id)
    if existing:
        return existing
    return _initialize_text(
        _text_id(f"thread:{thread_id}"),
        draft_id="",
        thread_id=thread_id,
        project_path=project_path,
        text_format=text_format,
        presentation=presentation,
        editor_kind=editor_kind,
    )


def initialize_text_draft(
    draft_id: str,
    project_path: str,
    *,
    text_format: Literal["plain", "markdown"] = "markdown",
    presentation: Literal["raw", "assisted"] = "assisted",
    editor_kind: Literal["text", "document"] | None = None,
) -> dict[str, Any]:
    return _initialize_text(
        _text_id(f"draft:{draft_id}"),
        draft_id=draft_id,
        thread_id="",
        project_path=project_path,
        text_format=text_format,
        presentation=presentation,
        editor_kind=editor_kind,
    )


def _initialize_text(
    text_id: str,
    *,
    draft_id: str,
    thread_id: str,
    project_path: str,
    text_format: str,
    presentation: str,
    editor_kind: str | None,
) -> dict[str, Any]:
    if text_format not in TEXT_FORMATS:
        raise ValueError("Unsupported text format.")
    if presentation not in TEXT_PRESENTATIONS:
        raise ValueError("Unsupported text presentation.")
    resolved_editor_kind = editor_kind or (
        "document" if text_format == "markdown" else "text"
    )
    if resolved_editor_kind not in TEXT_EDITOR_KINDS:
        raise ValueError("Unsupported editor kind.")
    if resolved_editor_kind == "text":
        text_format = "plain"
        presentation = "raw"
    else:
        text_format = "markdown"
    with _text_lock(text_id):
        existing = read_text_manifest(text_id)
        if existing:
            return existing
        now = _timestamp()
        manifest = {
            "schema_version": TEXT_SCHEMA_VERSION,
            "text_id": text_id,
            "draft_id": draft_id,
            "thread_id": thread_id,
            "project_path": project_path,
            "format": text_format,
            "presentation": presentation,
            "editor_kind": resolved_editor_kind,
            "current_revision": 0,
            "created_at": now,
            "updated_at": now,
        }
        directory = text_directory(text_id)
        extension = _extension(text_format)
        _atomic_write(directory / f"current.{extension}", b"")
        _write_json(directory / "manifest.json", manifest)
        return manifest


def bind_text_to_thread(text_id: str, thread_id: str) -> dict[str, Any]:
    with _text_lock(text_id):
        manifest = read_text_manifest(text_id)
        if not manifest:
            raise FileNotFoundError("Text manifest was not found.")
        conflict = text_manifest_for_thread(thread_id)
        if conflict and conflict.get("text_id") != text_id:
            raise ValueError("The Codex thread is already bound to another text.")
        manifest["thread_id"] = thread_id
        manifest["updated_at"] = _timestamp()
        _write_json(_manifest_path(text_id), manifest)
        return manifest


def update_text_presentation(text_id: str, presentation: str) -> dict[str, Any]:
    if presentation not in TEXT_PRESENTATIONS:
        raise ValueError("Unsupported text presentation.")
    with _text_lock(text_id):
        manifest = read_text_manifest(text_id)
        if not manifest:
            raise FileNotFoundError("Text manifest was not found.")
        manifest["presentation"] = (
            presentation if manifest.get("format") == "markdown" else "raw"
        )
        manifest["updated_at"] = _timestamp()
        _write_json(_manifest_path(text_id), manifest)
        return manifest


def load_text(text_id: str) -> str:
    manifest = read_text_manifest(text_id)
    if not manifest:
        raise FileNotFoundError("Text manifest was not found.")
    path = text_directory(text_id) / f"current.{_extension(str(manifest['format']))}"
    return path.read_text(encoding="utf-8")


def load_text_snapshot(text_id: str) -> tuple[dict[str, Any], str]:
    """Read a mutually consistent manifest and current document."""
    with _text_lock(text_id):
        manifest = read_text_manifest(text_id)
        if not manifest:
            raise FileNotFoundError("Text manifest was not found.")
        path = text_directory(text_id) / f"current.{_extension(str(manifest['format']))}"
        return manifest, path.read_text(encoding="utf-8")


def save_text(
    text_id: str,
    content: str,
    *,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    if not isinstance(content, str):
        raise TypeError("Text content must be a string.")
    with _text_lock(text_id):
        manifest = read_text_manifest(text_id)
        if not manifest:
            raise FileNotFoundError("Text manifest was not found.")
        current_revision = int(manifest.get("current_revision") or 0)
        if expected_revision is not None and expected_revision != current_revision:
            raise TextRevisionConflict(current_revision)
        extension = _extension(str(manifest["format"]))
        next_revision = current_revision + 1
        content_bytes = content.encode("utf-8")
        directory = text_directory(text_id)
        _atomic_write(directory / f"current.{extension}", content_bytes)
        _atomic_write(
            directory / "revisions" / f"{next_revision:08d}.{extension}",
            content_bytes,
        )
        manifest["current_revision"] = next_revision
        manifest["updated_at"] = _timestamp()
        _write_json(directory / "manifest.json", manifest)
        revisions = sorted((directory / "revisions").glob(f"*.{extension}"))
        for stale in revisions[:-TEXT_REVISION_LIMIT]:
            stale.unlink(missing_ok=True)
        return manifest


def text_file_references(text_id: str) -> dict[str, Any]:
    manifest = read_text_manifest(text_id)
    if not manifest:
        raise FileNotFoundError("Text manifest was not found.")
    extension = _extension(str(manifest["format"]))
    directory = text_directory(text_id)
    return {
        "document_path": str((directory / f"current.{extension}").resolve()),
        "filename": f"{text_id}.{extension}",
        "mime_type": "text/markdown" if extension == "md" else "text/plain",
    }


def save_text_export(text_id: str) -> dict[str, Any]:
    manifest = read_text_manifest(text_id)
    if not manifest:
        raise FileNotFoundError("Text manifest was not found.")
    content = load_text(text_id)
    extension = _extension(str(manifest["format"]))
    path = text_directory(text_id) / "exports" / f"{text_id}.{extension}"
    _atomic_write(path, content.encode("utf-8"))
    return {
        "format": "markdown" if extension == "md" else "txt",
        "export_path": str(path.resolve()),
        "filename": path.name,
        "byte_size": len(content.encode("utf-8")),
    }
