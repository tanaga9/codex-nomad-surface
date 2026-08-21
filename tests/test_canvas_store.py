import asyncio
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from starlette.websockets import WebSocketDisconnect

from codex_nomad_surface import canvas_runtime, canvas_store
from codex_nomad_surface.canvas_runtime import (
    CANVAS_DEVELOPER_INSTRUCTIONS,
    CanvasBroker,
    canvas_initial_context_items,
    canvas_dynamic_tool_handler,
    canvas_dynamic_tool_handler_for_canvas,
    canvas_dynamic_tools,
)


@pytest.fixture
def isolated_canvas_root(tmp_path, monkeypatch):
    monkeypatch.setattr(canvas_store, "CANVAS_ROOT", tmp_path / "canvases")
    return tmp_path / "canvases"


def test_canvas_snapshots_are_file_backed_and_revisioned(isolated_canvas_root):
    manifest = canvas_store.initialize_canvas("thread-file-store", "/path/to/project")
    canvas_id = manifest["canvas_id"]
    document = {
        "store": {
            "page:page": {"id": "page:page", "typeName": "page"},
            "shape:one": {
                "id": "shape:one",
                "typeName": "shape",
                "type": "geo",
            },
        }
    }

    preview_image = b"RIFF\x04\x00\x00\x00WEBP"
    saved = canvas_store.save_canvas_snapshot(
        canvas_id, document, "<svg />", preview_image
    )
    references = canvas_store.canvas_file_references(canvas_id)

    assert saved["current_revision"] == 1
    assert saved["project_path"] == "/path/to/project"
    assert canvas_store.load_canvas_document(canvas_id) == document
    assert canvas_store.load_canvas_preview(canvas_id) == "<svg />"
    assert canvas_store.load_canvas_visual_preview(canvas_id) == preview_image
    assert json.loads(Path(references["document_path"]).read_text(encoding="utf-8")) == document
    assert Path(references["preview_path"]).read_text(encoding="utf-8") == "<svg />"
    assert Path(references["visual_preview_path"]).read_bytes() == preview_image
    assert (
        isolated_canvas_root
        / canvas_id
        / "revisions"
        / "00000001"
        / "document.json"
    ).is_file()
    assert canvas_store.list_canvas_manifests()[0]["thread_id"] == "thread-file-store"


def test_canvas_save_updates_visual_preview_manifest_to_webp(isolated_canvas_root):
    manifest = canvas_store.initialize_canvas("thread-preview-manifest")
    canvas_id = manifest["canvas_id"]
    manifest_path = isolated_canvas_root / canvas_id / "manifest.json"
    legacy_manifest = {
        **manifest,
        "schema_version": 2,
        "visual_preview": "current/preview.png",
    }
    manifest_path.write_text(json.dumps(legacy_manifest), encoding="utf-8")

    saved = canvas_store.save_canvas_snapshot(canvas_id, {"store": {}})

    assert saved["schema_version"] == canvas_store.CANVAS_SCHEMA_VERSION
    assert saved["visual_preview"] == "current/preview.webp"
    assert json.loads(manifest_path.read_text(encoding="utf-8"))[
        "visual_preview"
    ] == "current/preview.webp"


def test_future_canvas_manifest_is_not_modified(isolated_canvas_root):
    manifest = canvas_store.initialize_canvas("thread-future-manifest")
    canvas_id = manifest["canvas_id"]
    manifest_path = isolated_canvas_root / canvas_id / "manifest.json"
    future_manifest = {
        **manifest,
        "schema_version": canvas_store.CANVAS_SCHEMA_VERSION + 1,
        "visual_preview": "current/preview.avif",
    }
    manifest_path.write_text(json.dumps(future_manifest), encoding="utf-8")
    original_manifest = manifest_path.read_bytes()

    with pytest.raises(canvas_store.CanvasManifestVersionError):
        canvas_store.initialize_canvas("thread-future-manifest")
    with pytest.raises(canvas_store.CanvasManifestVersionError):
        canvas_store.bind_canvas_to_thread(canvas_id, "thread-future-manifest")
    with pytest.raises(canvas_store.CanvasManifestVersionError):
        canvas_store.save_canvas_snapshot(canvas_id, {"store": {}})

    assert manifest_path.read_bytes() == original_manifest


def test_offline_read_scene_returns_saved_canvas(isolated_canvas_root):
    manifest = canvas_store.initialize_canvas("thread-offline-read")
    canvas_id = manifest["canvas_id"]
    canvas_store.save_canvas_snapshot(
        canvas_id,
        {
            "store": {
                "shape:one": {
                    "id": "shape:one",
                    "typeName": "shape",
                    "type": "text",
                }
            }
        },
        "<svg />",
        b"RIFF\x04\x00\x00\x00WEBP",
    )

    result = canvas_dynamic_tool_handler("thread-offline-read")(
        {"namespace": "canvas", "tool": "read_scene", "arguments": {}}
    )
    payload = json.loads(result["contentItems"][0]["text"])

    assert result["success"] is True
    assert payload["live"] is False
    assert payload["revision"] == 1
    assert payload["scene"]["shapes"][0]["id"] == "shape:one"
    assert payload["document_path"].endswith("revisions/00000001/document.json")
    assert result["contentItems"][1]["type"] == "inputImage"
    assert result["contentItems"][1]["imageUrl"].startswith(
        "data:image/webp;base64,"
    )


def test_draft_canvas_binds_to_thread_without_moving_files(isolated_canvas_root):
    manifest = canvas_store.initialize_canvas_draft(
        "draft-chat-1", "/path/to/project"
    )
    canvas_id = manifest["canvas_id"]
    document = {
        "store": {"shape:one": {"id": "shape:one", "typeName": "shape"}}
    }
    canvas_store.save_canvas_snapshot(canvas_id, document)

    bound = canvas_store.bind_canvas_to_thread(canvas_id, "thread-after-first-turn")

    assert bound["thread_id"] == "thread-after-first-turn"
    assert bound["draft_id"] == "draft-chat-1"
    assert canvas_store.load_canvas_document(canvas_id) == document
    assert canvas_store.canvas_manifest_for_thread("thread-after-first-turn") == bound
    assert canvas_store.canvas_exists_for_thread("thread-after-first-turn") is True


def test_dynamic_tool_handler_can_target_draft_canvas(isolated_canvas_root):
    manifest = canvas_store.initialize_canvas_draft("draft-chat-tool")
    canvas_id = manifest["canvas_id"]
    canvas_store.save_canvas_snapshot(
        canvas_id,
        {"store": {"shape:draft": {"id": "shape:draft", "typeName": "shape"}}},
    )

    result = canvas_dynamic_tool_handler_for_canvas(canvas_id)(
        {"namespace": "canvas", "tool": "read_scene", "arguments": {}}
    )
    payload = json.loads(result["contentItems"][0]["text"])

    assert result["success"] is True
    assert payload["scene"]["shapes"][0]["id"] == "shape:draft"


def test_live_read_scene_returns_visual_input_without_embedding_it_in_text(
    isolated_canvas_root, monkeypatch
):
    manifest = canvas_store.initialize_canvas("thread-live-visual")
    image_url = "data:image/webp;base64,UklGRg=="
    monkeypatch.setattr(
        canvas_runtime.CANVAS_BROKER,
        "call",
        lambda canvas_id, tool, arguments: {
            "ok": True,
            "payload": {
                "scene": {"shapes": []},
                "revision": 0,
                "preview_image_url": image_url,
            },
        },
    )

    result = canvas_dynamic_tool_handler_for_canvas(manifest["canvas_id"])(
        {"namespace": "canvas", "tool": "read_scene", "arguments": {}}
    )

    assert result["success"] is True
    assert result["contentItems"][1] == {
        "type": "inputImage",
        "imageUrl": image_url,
    }
    assert image_url not in result["contentItems"][0]["text"]


def test_canvas_preview_accepts_png_data_urls():
    preview, mime_type = canvas_runtime._decode_preview_image(
        "data:image/png;base64,iVBORw0KGgo="
    )

    assert preview == b"\x89PNG\r\n\x1a\n"
    assert mime_type == "image/png"


def test_canvas_preview_accepts_jpeg_data_urls():
    preview, mime_type = canvas_runtime._decode_preview_image(
        "data:image/jpeg;base64,/9j/"
    )

    assert preview == b"\xff\xd8\xff"
    assert mime_type == "image/jpeg"


def test_canvas_save_preserves_png_preview_mime_type(isolated_canvas_root):
    manifest = canvas_store.initialize_canvas("thread-png-preview")
    canvas_id = manifest["canvas_id"]
    document = {"store": {"shape:one": {"id": "shape:one", "typeName": "shape"}}}
    png_preview = b"\x89PNG\r\n\x1a\n"

    canvas_store.save_canvas_snapshot(
        canvas_id,
        document,
        "<svg />",
        png_preview,
        "image/png",
    )

    assert canvas_store.read_canvas_manifest(canvas_id)["visual_preview_mime_type"] == "image/png"
    assert canvas_store.canvas_visual_preview_data_url(canvas_id).startswith(
        "data:image/png;base64,"
    )


def test_canvas_save_preserves_jpeg_preview_mime_type(isolated_canvas_root):
    manifest = canvas_store.initialize_canvas("thread-jpeg-preview")
    canvas_id = manifest["canvas_id"]
    document = {"store": {"shape:one": {"id": "shape:one", "typeName": "shape"}}}
    jpeg_preview = b"\xff\xd8\xff"

    canvas_store.save_canvas_snapshot(
        canvas_id,
        document,
        "<svg />",
        jpeg_preview,
        "image/jpeg",
    )

    assert (
        canvas_store.read_canvas_manifest(canvas_id)["visual_preview_mime_type"]
        == "image/jpeg"
    )
    assert canvas_store.canvas_visual_preview_data_url(canvas_id).startswith(
        "data:image/jpeg;base64,"
    )


def test_canvas_preview_rejects_unsupported_data_urls():
    with pytest.raises(ValueError, match="WebP, JPEG, or PNG data URL"):
        canvas_runtime._decode_preview_image("data:image/svg+xml;base64,PHN2Zy8+")


def test_rejected_visual_preview_does_not_block_document_save(
    isolated_canvas_root, monkeypatch
):
    manifest = canvas_store.initialize_canvas("thread-rejected-visual")
    canvas_id = manifest["canvas_id"]
    document = {
        "store": {"shape:safe": {"id": "shape:safe", "typeName": "shape"}}
    }
    monkeypatch.setattr(canvas_runtime, "CANVAS_PREVIEW_IMAGE_MAX_BYTES", 2)

    saved, preview_error = canvas_runtime._save_canvas_payload(
        canvas_id,
        document,
        "<svg />",
        "data:image/webp;base64,aW1hZ2U=",
    )

    assert saved["current_revision"] == 1
    assert preview_error == "Canvas preview is too large."
    assert canvas_store.load_canvas_document(canvas_id) == document
    assert canvas_store.load_canvas_preview(canvas_id) == "<svg />"
    assert canvas_store.load_canvas_visual_preview(canvas_id) == b""


def test_rejected_visual_preview_preserves_valid_image_for_same_document(
    isolated_canvas_root, monkeypatch
):
    manifest = canvas_store.initialize_canvas("thread-preserved-visual")
    canvas_id = manifest["canvas_id"]
    document = {
        "store": {"shape:safe": {"id": "shape:safe", "typeName": "shape"}}
    }
    preview_image = b"valid preview"
    canvas_store.save_canvas_snapshot(
        canvas_id,
        document,
        "<svg />",
        preview_image,
    )
    monkeypatch.setattr(canvas_runtime, "CANVAS_PREVIEW_IMAGE_MAX_BYTES", 2)

    saved, preview_error = canvas_runtime._save_canvas_payload(
        canvas_id,
        document,
        "<svg />",
        "data:image/webp;base64,aW1hZ2U=",
    )

    assert saved["current_revision"] == 1
    assert preview_error == "Canvas preview is too large."
    assert canvas_store.load_canvas_visual_preview(canvas_id) == preview_image

    saved, preview_error = canvas_runtime._save_canvas_payload(
        canvas_id,
        document,
        "<svg />",
        "",
        "Browser image export failed.",
    )

    assert saved["current_revision"] == 1
    assert preview_error == "Browser image export failed."
    assert canvas_store.load_canvas_visual_preview(canvas_id) == preview_image


def test_empty_canvas_clears_saved_preview(isolated_canvas_root):
    manifest = canvas_store.initialize_canvas("thread-preview-clear")
    canvas_id = manifest["canvas_id"]
    canvas_store.save_canvas_snapshot(
        canvas_id,
        {"store": {"shape:one": {"id": "shape:one", "typeName": "shape"}}},
        "<svg>old preview</svg>",
        b"old preview image",
    )

    canvas_store.save_canvas_snapshot(canvas_id, {"store": {}}, "")

    assert canvas_store.load_canvas_preview(canvas_id) == ""
    assert canvas_store.load_canvas_visual_preview(canvas_id) == b""


def test_canvas_save_reuses_an_incomplete_next_revision(isolated_canvas_root):
    manifest = canvas_store.initialize_canvas("thread-interrupted-save")
    canvas_id = manifest["canvas_id"]
    revision_directory = (
        isolated_canvas_root / canvas_id / "revisions" / "00000001"
    )
    revision_directory.mkdir(parents=True)
    (revision_directory / "document.json").write_text(
        "incomplete", encoding="utf-8"
    )

    document = {"store": {"shape:recovered": {"typeName": "shape"}}}
    saved = canvas_store.save_canvas_snapshot(canvas_id, document, "<svg />")

    assert saved["current_revision"] == 1
    assert canvas_store.load_canvas_document(canvas_id) == document
    assert json.loads(
        (revision_directory / "document.json").read_text(encoding="utf-8")
    ) == document
    assert (revision_directory / "metadata.json").is_file()


def test_canvas_broker_retires_the_previous_connection():
    broker = CanvasBroker()
    first = broker.register("canvas-one")

    second = broker.register("canvas-one")

    assert first.retired.is_set()
    assert first.outgoing.get_nowait() is None
    assert broker.is_current("canvas-one", first) is False
    assert broker.is_current("canvas-one", second) is True


def test_canvas_dynamic_tool_manifest_uses_namespace_shape():
    namespace = canvas_dynamic_tools()[0]

    assert namespace["type"] == "namespace"
    assert namespace["name"] == "canvas"
    assert "Nomad Surface embedded Canvas" in namespace["description"]
    assert [tool["name"] for tool in namespace["tools"]] == [
        "read_scene",
        "apply_patch",
    ]
    assert "page-space bounds" in namespace["tools"][0]["description"]
    assert "whole-canvas image" in namespace["tools"][0]["description"]
    apply_schema = namespace["tools"][1]["inputSchema"]
    assert apply_schema["required"] == ["command_id", "base_revision", "operations"]
    assert apply_schema["properties"]["command_id"] == {
        "type": "string",
        "minLength": 1,
        "maxLength": 128,
    }
    operations = apply_schema["properties"]["operations"]
    assert operations["minItems"] == 1
    assert operations["maxItems"] == 100
    operation_schemas = operations["items"]["oneOf"]
    assert [item["properties"]["op"]["const"] for item in operation_schemas] == [
        "create",
        "update",
        "move",
        "resize",
        "delete",
        "connect",
    ]
    assert all(item["additionalProperties"] is False for item in operation_schemas)
    create_shapes = operation_schemas[0]["properties"]["shape"]["oneOf"]
    assert [item["properties"]["type"]["const"] for item in create_shapes] == [
        "geo",
        "text",
        "note",
        "frame",
    ]
    assert all(item["additionalProperties"] is False for item in create_shapes)


def test_canvas_apply_patch_schema_accepts_nomad_dto_and_rejects_raw_props():
    schema = canvas_runtime._canvas_apply_patch_schema()
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    valid = {
        "command_id": "diagram-command",
        "base_revision": 4,
        "operations": [
            {
                "op": "create",
                "ref": "gate",
                "shape": {
                    "type": "geo",
                    "x": 120,
                    "y": 200,
                    "width": 280,
                    "height": 120,
                    "text": "Gate",
                    "style": {"geo": "diamond", "fill": "semi"},
                },
            },
            {
                "op": "connect",
                "ref": "edge",
                "from": {"ref": "gate"},
                "to": {"id": "shape:existing"},
            },
        ],
    }
    raw_props = {
        **valid,
        "operations": [
            {
                "op": "create",
                "ref": "gate",
                "shape": {
                    "type": "geo",
                    "x": 0,
                    "y": 0,
                    "props": {"text": "not allowed"},
                },
            }
        ],
    }

    assert list(validator.iter_errors(valid)) == []
    assert list(validator.iter_errors(raw_props))

    empty_update = {
        "command_id": "empty-update",
        "base_revision": 0,
        "operations": [
            {"op": "update", "target": {"id": "shape:existing"}}
        ],
    }
    empty_style_update = {
        **empty_update,
        "operations": [
            {
                "op": "update",
                "target": {"id": "shape:existing"},
                "style": {},
            }
        ],
    }
    valid_update = {
        **empty_update,
        "operations": [
            {
                "op": "update",
                "target": {"id": "shape:existing"},
                "text": "updated",
            }
        ],
    }

    assert list(validator.iter_errors(empty_update))
    assert list(validator.iter_errors(empty_style_update))
    assert list(validator.iter_errors(valid_update)) == []


def test_canvas_command_receipt_is_committed_and_replayed(isolated_canvas_root):
    manifest = canvas_store.initialize_canvas("thread-command-replay")
    canvas_id = manifest["canvas_id"]
    arguments = {
        "command_id": "diagram-command-1",
        "base_revision": 0,
        "operations": [
            {
                "op": "create",
                "ref": "gate",
                "shape": {"type": "geo", "x": 10, "y": 20},
            }
        ],
    }
    input_hash = canvas_runtime._canvas_command_input_hash(arguments)
    saved = canvas_store.save_canvas_snapshot(
        canvas_id,
        {"store": {"shape:gate": {"id": "shape:gate", "typeName": "shape"}}},
        command_receipt={
            "command_id": arguments["command_id"],
            "input_hash": input_hash,
            "base_revision": 0,
            "changed_ids": ["shape:gate"],
            "refs": {"gate": "shape:gate"},
            "warnings": [],
        },
    )

    receipt = canvas_store.read_canvas_command_receipt(
        canvas_id, arguments["command_id"]
    )
    result = canvas_dynamic_tool_handler_for_canvas(canvas_id)(
        {"namespace": "canvas", "tool": "apply_patch", "arguments": arguments}
    )
    payload = json.loads(result["contentItems"][0]["text"])

    assert saved["current_revision"] == 1
    assert receipt["result_revision"] == 1
    assert receipt["input_hash"] == input_hash
    receipt_path = canvas_store._command_receipt_path(
        canvas_id, arguments["command_id"]
    )
    assert receipt_path.parent.name == "commands"
    assert arguments["command_id"] not in receipt_path.name
    assert result["success"] is True
    assert payload["replayed"] is True
    assert payload["revision"] == 1
    assert payload["refs"] == {"gate": "shape:gate"}
    assert canvas_store.read_canvas_manifest(canvas_id)["current_revision"] == 1


def test_canvas_command_id_conflict_precedes_revision_conflict(isolated_canvas_root):
    manifest = canvas_store.initialize_canvas("thread-command-conflict")
    canvas_id = manifest["canvas_id"]
    original = {
        "command_id": "shared-command",
        "base_revision": 0,
        "operations": [
            {
                "op": "create",
                "ref": "first",
                "shape": {"type": "geo", "x": 0, "y": 0},
            }
        ],
    }
    canvas_store.save_canvas_snapshot(
        canvas_id,
        {"store": {"shape:first": {"id": "shape:first", "typeName": "shape"}}},
        command_receipt={
            "command_id": original["command_id"],
            "input_hash": canvas_runtime._canvas_command_input_hash(original),
            "base_revision": 0,
            "changed_ids": ["shape:first"],
            "refs": {"first": "shape:first"},
            "warnings": [],
        },
    )
    changed = {
        **original,
        "operations": [
            {
                "op": "create",
                "ref": "different",
                "shape": {"type": "geo", "x": 20, "y": 20},
            }
        ],
    }

    result = canvas_dynamic_tool_handler_for_canvas(canvas_id)(
        {"namespace": "canvas", "tool": "apply_patch", "arguments": changed}
    )
    payload = json.loads(result["contentItems"][0]["text"])

    assert result["success"] is False
    assert payload == {"error": "command_id_conflict", "result_revision": 1}


def test_missing_canvas_command_receipt_is_reconstructed(isolated_canvas_root):
    manifest = canvas_store.initialize_canvas("thread-command-reconstruct")
    canvas_id = manifest["canvas_id"]
    command_id = "recover-command"
    receipt_data = {
        "command_id": command_id,
        "input_hash": "sha256:" + ("a" * 64),
        "base_revision": 0,
        "changed_ids": ["shape:recover"],
        "refs": {"recover": "shape:recover"},
        "warnings": [],
    }
    canvas_store.save_canvas_snapshot(
        canvas_id,
        {"store": {"shape:recover": {"id": "shape:recover", "typeName": "shape"}}},
        command_receipt=receipt_data,
    )
    receipt_path = canvas_store._command_receipt_path(canvas_id, command_id)
    receipt_path.unlink()

    reconstructed = canvas_store.read_canvas_command_receipt(canvas_id, command_id)
    metadata_path = (
        isolated_canvas_root
        / canvas_id
        / "revisions"
        / "00000001"
        / "metadata.json"
    )

    assert reconstructed["command_id"] == command_id
    assert reconstructed["result_revision"] == 1
    assert receipt_path.is_file()
    assert json.loads(metadata_path.read_text(encoding="utf-8"))[
        "command_receipt"
    ] == reconstructed


def test_receipt_cache_failure_after_commit_does_not_reject_command(
    isolated_canvas_root, monkeypatch
):
    manifest = canvas_store.initialize_canvas("thread-receipt-cache-failure")
    canvas_id = manifest["canvas_id"]
    command_id = "cache-write-failure"
    original_atomic_write = canvas_store._atomic_write

    def fail_receipt_cache(path, content):
        if path.parent.name == "commands":
            raise OSError("receipt cache is unavailable")
        original_atomic_write(path, content)

    monkeypatch.setattr(canvas_store, "_atomic_write", fail_receipt_cache)

    saved = canvas_store.save_canvas_snapshot(
        canvas_id,
        {"store": {"shape:cached": {"id": "shape:cached", "typeName": "shape"}}},
        command_receipt={
            "command_id": command_id,
            "input_hash": "sha256:" + ("c" * 64),
            "base_revision": 0,
            "changed_ids": ["shape:cached"],
            "refs": {},
            "warnings": [],
        },
    )

    receipt_path = canvas_store._command_receipt_path(canvas_id, command_id)
    reconstructed = canvas_store.read_canvas_command_receipt(canvas_id, command_id)

    assert saved["current_revision"] == 1
    assert canvas_store.read_canvas_manifest(canvas_id)["current_revision"] == 1
    assert reconstructed["result_revision"] == 1
    assert receipt_path.exists() is False


def test_revision_prune_failure_after_commit_does_not_reject_command(
    isolated_canvas_root, monkeypatch
):
    manifest = canvas_store.initialize_canvas("thread-prune-failure")
    canvas_id = manifest["canvas_id"]
    command_id = "prune-failure"

    def fail_prune(_directory):
        raise OSError("revision cleanup is unavailable")

    monkeypatch.setattr(canvas_store, "_prune_revisions", fail_prune)

    saved = canvas_store.save_canvas_snapshot(
        canvas_id,
        {"store": {"shape:saved": {"id": "shape:saved", "typeName": "shape"}}},
        command_receipt={
            "command_id": command_id,
            "input_hash": "sha256:" + ("d" * 64),
            "base_revision": 0,
            "changed_ids": ["shape:saved"],
            "refs": {},
            "warnings": [],
        },
    )

    assert saved["current_revision"] == 1
    assert canvas_store.read_canvas_command_receipt(canvas_id, command_id)[
        "result_revision"
    ] == 1


def test_manifest_failure_does_not_expose_uncommitted_document(
    isolated_canvas_root, monkeypatch
):
    manifest = canvas_store.initialize_canvas("thread-manifest-failure")
    canvas_id = manifest["canvas_id"]
    committed = {"store": {"shape:old": {"id": "shape:old", "typeName": "shape"}}}
    uncommitted = {
        "store": {"shape:new": {"id": "shape:new", "typeName": "shape"}}
    }
    canvas_store.save_canvas_snapshot(canvas_id, committed)
    current_path = isolated_canvas_root / canvas_id / "current" / "document.json"
    original_atomic_write = canvas_store._atomic_write

    def fail_manifest(path, content):
        if path.name == "manifest.json":
            raise OSError("manifest commit failed")
        original_atomic_write(path, content)

    monkeypatch.setattr(canvas_store, "_atomic_write", fail_manifest)

    with pytest.raises(OSError, match="manifest commit failed"):
        canvas_store.save_canvas_snapshot(
            canvas_id,
            uncommitted,
            expected_revision=1,
            command_receipt={
                "command_id": "interrupted-command",
                "input_hash": "sha256:" + ("f" * 64),
                "base_revision": 1,
                "changed_ids": ["shape:new"],
                "refs": {},
                "warnings": [],
            },
        )

    assert canvas_store.read_canvas_manifest(canvas_id)["current_revision"] == 1
    assert canvas_store.load_canvas_document(canvas_id) == committed
    assert json.loads(current_path.read_text(encoding="utf-8")) == committed
    assert (
        canvas_store.read_canvas_command_receipt(canvas_id, "interrupted-command")
        is None
    )


def test_current_cache_failure_after_commit_keeps_canonical_document_readable(
    isolated_canvas_root, monkeypatch
):
    manifest = canvas_store.initialize_canvas("thread-current-cache-failure")
    canvas_id = manifest["canvas_id"]
    document = {
        "store": {"shape:saved": {"id": "shape:saved", "typeName": "shape"}}
    }
    original_atomic_write = canvas_store._atomic_write

    def fail_current_cache(path, content):
        if path.parent.name == "current":
            raise OSError("current cache is unavailable")
        original_atomic_write(path, content)

    monkeypatch.setattr(canvas_store, "_atomic_write", fail_current_cache)

    saved = canvas_store.save_canvas_snapshot(
        canvas_id,
        document,
        command_receipt={
            "command_id": "committed-without-cache",
            "input_hash": "sha256:" + ("1" * 64),
            "base_revision": 0,
            "changed_ids": ["shape:saved"],
            "refs": {},
            "warnings": [],
        },
    )

    references = canvas_store.canvas_file_references(canvas_id)
    assert saved["current_revision"] == 1
    assert saved["document"] == "revisions/00000001/document.json"
    assert canvas_store.load_canvas_document(canvas_id) == document
    assert references["document_path"].endswith(
        "revisions/00000001/document.json"
    )
    assert canvas_store.read_canvas_command_receipt(
        canvas_id, "committed-without-cache"
    )["result_revision"] == 1


def test_noop_command_gets_its_own_revision_and_reconstructable_receipt(
    isolated_canvas_root,
):
    manifest = canvas_store.initialize_canvas("thread-noop-reconstruct")
    canvas_id = manifest["canvas_id"]
    document = {"store": {"shape:same": {"id": "shape:same", "typeName": "shape"}}}
    canvas_store.save_canvas_snapshot(canvas_id, document)
    command_id = "noop-command"
    canvas_store.save_canvas_snapshot(
        canvas_id,
        document,
        command_receipt={
            "command_id": command_id,
            "input_hash": "sha256:" + ("b" * 64),
            "base_revision": 1,
            "changed_ids": ["shape:same"],
            "refs": {},
            "warnings": [],
        },
    )
    receipt_path = canvas_store._command_receipt_path(canvas_id, command_id)
    receipt_path.unlink()

    reconstructed = canvas_store.read_canvas_command_receipt(canvas_id, command_id)

    assert reconstructed["result_revision"] == 2
    assert canvas_store.read_canvas_manifest(canvas_id)["current_revision"] == 2
    assert receipt_path.is_file()


def test_corrupt_canvas_command_receipt_fails_closed(isolated_canvas_root):
    manifest = canvas_store.initialize_canvas("thread-command-corrupt")
    canvas_id = manifest["canvas_id"]
    command_id = "corrupt-command"
    receipt_path = canvas_store._command_receipt_path(canvas_id, command_id)
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text('{"schema_version": 999}', encoding="utf-8")

    with pytest.raises(canvas_store.CanvasCommandReceiptError):
        canvas_store.read_canvas_command_receipt(canvas_id, command_id)

    result = canvas_dynamic_tool_handler_for_canvas(canvas_id)(
        {
            "namespace": "canvas",
            "tool": "apply_patch",
            "arguments": {
                "command_id": command_id,
                "base_revision": 0,
                "operations": [
                    {
                        "op": "create",
                        "ref": "shape",
                        "shape": {"type": "geo", "x": 0, "y": 0},
                    }
                ],
            },
        }
    )
    payload = json.loads(result["contentItems"][0]["text"])

    assert result["success"] is False
    assert payload["error"] == "receipt_corrupt"


def test_apply_response_without_document_fails_closed_and_is_not_receipted(
    isolated_canvas_root, monkeypatch
):
    manifest = canvas_store.initialize_canvas("thread-invalid-apply-response")
    canvas_id = manifest["canvas_id"]
    broker = CanvasBroker()
    monkeypatch.setattr(canvas_runtime, "CANVAS_BROKER", broker)
    monkeypatch.setattr(canvas_runtime, "auth_required", lambda: False)

    async def exercise_websocket():
        request_id = "request-without-document"
        pending = canvas_runtime.PendingCanvasRequest(
            context={
                "command_id": "invalid-response-command",
                "input_hash": "sha256:" + ("c" * 64),
                "base_revision": 0,
            }
        )
        ack_received = asyncio.Event()

        class FakeWebSocket:
            scope = {}
            path_params = {"canvas_id": canvas_id}
            receive_count = 0
            sent_messages = []

            async def accept(self):
                return None

            async def close(self, code=1000):
                return None

            async def receive_json(self):
                if self.receive_count == 0:
                    self.receive_count += 1
                    with broker._lock:
                        broker._pending[request_id] = pending
                    return {
                        "type": "response",
                        "id": request_id,
                        "ok": True,
                        "payload": {"changed_ids": [], "refs": {}, "warnings": []},
                    }
                await asyncio.wait_for(ack_received.wait(), timeout=1)
                raise WebSocketDisconnect()

            async def send_json(self, message):
                self.sent_messages.append(message)
                if message.get("type") == "response_ack":
                    ack_received.set()

        websocket = FakeWebSocket()
        await canvas_runtime.canvas_websocket(websocket)
        return pending, websocket.sent_messages

    pending, sent_messages = asyncio.run(exercise_websocket())

    assert pending.result == {
        "ok": False,
        "error": "canvas_invalid_response: apply result has no document",
        "payload": {
            "error": "canvas_invalid_response",
            "message": "A successful apply response must include a document.",
        },
    }
    assert any(
        message.get("type") == "response_ack" and message.get("ok") is False
        for message in sent_messages
    )
    assert canvas_store.read_canvas_manifest(canvas_id)["current_revision"] == 0
    assert (
        canvas_store.read_canvas_command_receipt(
            canvas_id, "invalid-response-command"
        )
        is None
    )


def test_canvas_command_status_reconciles_a_lost_commit_ack(isolated_canvas_root):
    manifest = canvas_store.initialize_canvas("thread-command-status")
    canvas_id = manifest["canvas_id"]
    arguments = {
        "command_id": "status-command",
        "base_revision": 0,
        "operations": [
            {
                "op": "create",
                "ref": "status-shape",
                "shape": {"type": "geo", "x": 0, "y": 0},
            }
        ],
    }

    assert canvas_runtime._canvas_command_status(canvas_id, arguments) == {
        "ok": False,
        "error": "command_not_committed",
    }

    canvas_store.save_canvas_snapshot(
        canvas_id,
        {"store": {"shape:status": {"id": "shape:status", "typeName": "shape"}}},
        command_receipt={
            "command_id": arguments["command_id"],
            "input_hash": canvas_runtime._canvas_command_input_hash(arguments),
            "base_revision": 0,
            "changed_ids": ["shape:status"],
            "refs": {"status-shape": "shape:status"},
            "warnings": [],
        },
    )

    assert canvas_runtime._canvas_command_status(canvas_id, arguments) == {
        "ok": True,
        "error": "",
    }
    assert canvas_runtime._canvas_command_status(
        canvas_id,
        {**arguments, "operations": [{"op": "delete", "targets": []}]},
    ) == {"ok": False, "error": "command_id_conflict"}


def test_canvas_initial_context_is_a_developer_message():
    items = canvas_initial_context_items()

    assert items == [
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
    assert "canvas dynamic tools as the primary interface" in items[0]["content"][0][
        "text"
    ]
    assert "whole-canvas image" in items[0]["content"][0]["text"]
