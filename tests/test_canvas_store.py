import json
from pathlib import Path

import pytest

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
    assert payload["document_path"].endswith("current/document.json")
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


def test_canvas_preview_rejects_png_data_urls():
    with pytest.raises(ValueError, match="WebP data URL"):
        canvas_runtime._decode_preview_image("data:image/png;base64,iVBORw0KGgo=")


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
