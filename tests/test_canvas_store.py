import asyncio
import json
import threading
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


def _obsidian_markdown(label: str = "drawing") -> str:
    return (
        "---\ntldraw-file: true\ntags:\n  - tldraw\n---\n\n"
        "```json "
        f"{canvas_store.CANVAS_OBSIDIAN_START_MARKER}\n"
        f'{{"meta":{{"label":"{label}"}},"raw":{{}}}}\n'
        f"{canvas_store.CANVAS_OBSIDIAN_END_MARKER}\n```\n"
    )


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


def test_canvas_obsidian_export_is_bounded_and_atomically_replaced(
    isolated_canvas_root, monkeypatch
):
    manifest = canvas_store.initialize_canvas("thread-obsidian-export")
    canvas_id = manifest["canvas_id"]
    first = _obsidian_markdown("first")
    second = _obsidian_markdown("second")

    saved = canvas_store.save_canvas_obsidian_export(canvas_id, first)
    export_path = Path(saved["export_path"])

    assert export_path == (
        isolated_canvas_root / canvas_id / "exports" / f"{canvas_id}.md"
    ).resolve()
    assert export_path.read_text(encoding="utf-8") == first
    assert saved["format"] == "obsidian"
    assert saved["filename"] == f"{canvas_id}.md"
    assert saved["byte_size"] == len(first.encode("utf-8"))
    assert saved["content_hash"].startswith("sha256:")

    canvas_store.save_canvas_obsidian_export(canvas_id, second)
    assert export_path.read_text(encoding="utf-8") == second
    assert list(export_path.parent.iterdir()) == [export_path]

    with pytest.raises(ValueError, match="format"):
        canvas_store.save_canvas_obsidian_export(canvas_id, "not a drawing")
    monkeypatch.setattr(canvas_store, "CANVAS_OBSIDIAN_EXPORT_MAX_BYTES", 8)
    with pytest.raises(ValueError, match="size"):
        canvas_store.save_canvas_obsidian_export(canvas_id, first)


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
                    "page:one": {"id": "page:one", "typeName": "page"},
                    "shape:one": {
                        "id": "shape:one",
                        "typeName": "shape",
                        "type": "text",
                        "parentId": "page:one",
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
    assert payload["shapes"][0]["id"] == "shape:one"
    assert payload["scope"] == {"type": "all"}
    assert payload["detail"] == "compact"
    assert payload["document_path"].endswith("revisions/00000001/document.json")
    assert result["contentItems"][1]["type"] == "inputImage"
    assert result["contentItems"][1]["imageUrl"].startswith(
        "data:image/webp;base64,"
    )


def test_offline_scoped_reads_resolve_frames_and_fail_closed_for_nested_bounds(
    isolated_canvas_root,
):
    manifest = canvas_store.initialize_canvas("thread-offline-scopes")
    canvas_id = manifest["canvas_id"]
    document = {
        "store": {
            "page:one": {"id": "page:one", "typeName": "page"},
            "page:two": {"id": "page:two", "typeName": "page"},
            "shape:frame": {
                "id": "shape:frame",
                "typeName": "shape",
                "type": "frame",
                "parentId": "page:two",
                "index": "a1",
                "x": 0,
                "y": 0,
                "props": {"w": 400, "h": 300, "name": "Process"},
            },
            "shape:child": {
                "id": "shape:child",
                "typeName": "shape",
                "type": "geo",
                "parentId": "shape:frame",
                "index": "a2",
                "x": 20,
                "y": 20,
                "props": {
                    "w": 100,
                    "h": 60,
                    "richText": {
                        "type": "doc",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [
                                    {"type": "text", "text": "Child label"}
                                ],
                            }
                        ],
                    },
                },
            },
            "shape:outside": {
                "id": "shape:outside",
                "typeName": "shape",
                "type": "arrow",
                "parentId": "page:two",
                "index": "a3",
                "x": 700,
                "y": 700,
                "props": {"start": {"x": 0, "y": 0}, "end": {"x": 20, "y": 20}},
            },
            "binding:outside-child": {
                "id": "binding:outside-child",
                "typeName": "binding",
                "type": "arrow",
                "fromId": "shape:outside",
                "toId": "shape:child",
                "props": {},
            },
        }
    }
    canvas_store.save_canvas_snapshot(canvas_id, document)
    handler = canvas_dynamic_tool_handler_for_canvas(canvas_id)

    frame_result = handler(
        {
            "namespace": "canvas",
            "tool": "read_scene",
            "arguments": {
                "scope": {"type": "frame", "id": "shape:frame"},
                "include_image": True,
            },
        }
    )
    frame_payload = json.loads(frame_result["contentItems"][0]["text"])
    bounds_result = handler(
        {
            "namespace": "canvas",
            "tool": "read_scene",
            "arguments": {
                "scope": {"type": "bounds", "x": 10, "y": 10, "width": 150, "height": 100},
                "include_image": False,
            },
        }
    )
    bounds_payload = json.loads(bounds_result["contentItems"][0]["text"])

    assert {shape["id"] for shape in frame_payload["shapes"]} == {
        "shape:frame",
        "shape:child",
    }
    assert frame_payload["page_id"] == "page:two"
    assert frame_payload["image"]["error"] == "scoped_preview_requires_live_editor"
    assert len(frame_result["contentItems"]) == 1
    assert frame_payload["bindings"][0]["from"]["external_to_scope"] is True
    assert frame_payload["bindings"][0]["to"]["external_to_scope"] is False
    assert next(
        shape["text"]
        for shape in frame_payload["shapes"]
        if shape["id"] == "shape:child"
    ) == "Child label"
    assert "live editor for page bounds" in frame_payload["warnings"][0]
    assert bounds_result["success"] is False
    assert bounds_payload["error"] == "scope_requires_live_editor"


def test_offline_bounds_resolve_top_level_unrotated_shapes(isolated_canvas_root):
    manifest = canvas_store.initialize_canvas("thread-offline-simple-bounds")
    canvas_id = manifest["canvas_id"]
    canvas_store.save_canvas_snapshot(
        canvas_id,
        {
            "store": {
                "page:one": {"id": "page:one", "typeName": "page"},
                "shape:inside": {
                    "id": "shape:inside",
                    "typeName": "shape",
                    "type": "geo",
                    "parentId": "page:one",
                    "index": "a1",
                    "x": 20,
                    "y": 20,
                    "rotation": 0,
                    "props": {"w": 50, "h": 40},
                },
                "shape:outside": {
                    "id": "shape:outside",
                    "typeName": "shape",
                    "type": "geo",
                    "parentId": "page:one",
                    "index": "a2",
                    "x": 500,
                    "y": 500,
                    "rotation": 0,
                    "props": {"w": 50, "h": 40},
                },
            }
        },
    )
    result = canvas_dynamic_tool_handler_for_canvas(canvas_id)(
        {
            "namespace": "canvas",
            "tool": "read_scene",
            "arguments": {
                "scope": {
                    "type": "bounds",
                    "x": 0,
                    "y": 0,
                    "width": 100,
                    "height": 100,
                },
                "include_image": False,
            },
        }
    )
    payload = json.loads(result["contentItems"][0]["text"])

    assert result["success"] is True
    assert [shape["id"] for shape in payload["shapes"]] == ["shape:inside"]


def test_offline_viewport_and_selection_scopes_require_live_editor(
    isolated_canvas_root,
):
    manifest = canvas_store.initialize_canvas("thread-live-scopes")
    handler = canvas_dynamic_tool_handler_for_canvas(manifest["canvas_id"])

    for scope_type in ("viewport", "selection"):
        result = handler(
            {
                "namespace": "canvas",
                "tool": "read_scene",
                "arguments": {"scope": {"type": scope_type}},
            }
        )
        payload = json.loads(result["contentItems"][0]["text"])
        assert result["success"] is False
        assert payload["error"] == "scope_requires_live_editor"


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
        {
            "store": {
                "page:one": {"id": "page:one", "typeName": "page"},
                "shape:draft": {
                    "id": "shape:draft",
                    "typeName": "shape",
                    "parentId": "page:one",
                },
            }
        },
    )

    result = canvas_dynamic_tool_handler_for_canvas(canvas_id)(
        {"namespace": "canvas", "tool": "read_scene", "arguments": {}}
    )
    payload = json.loads(result["contentItems"][0]["text"])

    assert result["success"] is True
    assert payload["shapes"][0]["id"] == "shape:draft"


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


def test_canvas_export_tool_requires_obsidian_format_and_live_editor(
    isolated_canvas_root, monkeypatch
):
    manifest = canvas_store.initialize_canvas("thread-live-export")
    calls = []

    class RecordingBroker:
        def call(self, canvas_id, tool, arguments, *, context=None):
            calls.append((canvas_id, tool, arguments, context))
            return {
                "ok": True,
                "payload": {
                    "live": True,
                    "format": "obsidian",
                    "filename": f"{canvas_id}.md",
                    "export_path": f"/managed/{canvas_id}.md",
                    "byte_size": 123,
                },
            }

    monkeypatch.setattr(canvas_runtime, "CANVAS_BROKER", RecordingBroker())
    handler = canvas_dynamic_tool_handler_for_canvas(manifest["canvas_id"])

    result = handler(
        {
            "namespace": "canvas",
            "tool": "export",
            "arguments": {"format": "obsidian"},
        }
    )
    payload = json.loads(result["contentItems"][0]["text"])

    assert result["success"] is True
    assert payload["format"] == "obsidian"
    assert payload["export_path"].endswith(f"{manifest['canvas_id']}.md")
    assert calls == [
        (
            manifest["canvas_id"],
            "export",
            {"format": "obsidian"},
            {"method": "export"},
        )
    ]

    invalid = handler(
        {
            "namespace": "canvas",
            "tool": "export",
            "arguments": {"format": "json"},
        }
    )
    assert invalid["success"] is False
    assert json.loads(invalid["contentItems"][0]["text"])["error"] == (
        "export_validation_failed"
    )
    assert len(calls) == 1

    monkeypatch.setattr(canvas_runtime, "CANVAS_BROKER", CanvasBroker())
    unavailable = handler(
        {
            "namespace": "canvas",
            "tool": "export",
            "arguments": {"format": "obsidian"},
        }
    )
    assert unavailable["success"] is False
    assert json.loads(unavailable["contentItems"][0]["text"])["error"] == (
        "canvas_unavailable"
    )


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


def test_document_only_websocket_save_preserves_saved_preview(
    isolated_canvas_root, monkeypatch
):
    manifest = canvas_store.initialize_canvas("thread-document-only-save")
    canvas_id = manifest["canvas_id"]
    preview_svg = "<svg>saved preview</svg>"
    preview_image = b"saved preview image"
    canvas_store.save_canvas_snapshot(
        canvas_id,
        {"store": {"shape:one": {"id": "shape:one", "typeName": "shape"}}},
        preview_svg,
        preview_image,
        "image/png",
    )

    monkeypatch.setattr(canvas_runtime, "auth_required", lambda: False)

    class FakeWebSocket:
        scope = {}
        path_params = {"canvas_id": canvas_id}
        query_params = {"owner_id": "document-save", "generation": "1"}
        received = False

        async def accept(self):
            return None

        async def close(self, code=1000):
            return None

        async def receive_json(self):
            if not self.received:
                self.received = True
                return {
                    "type": "snapshot",
                    "document": {
                        "store": {
                            "shape:two": {
                                "id": "shape:two",
                                "typeName": "shape",
                            }
                        }
                    },
                }
            raise WebSocketDisconnect()

        async def send_json(self, message):
            return None

    asyncio.run(canvas_runtime.canvas_websocket(FakeWebSocket()))
    saved = canvas_store.read_canvas_manifest(canvas_id)
    references = canvas_store.canvas_file_references(canvas_id)

    assert saved["current_revision"] == 2
    assert saved["preview_content_hash"] != saved["content_hash"]
    assert saved["visual_preview_content_hash"] != saved["content_hash"]
    assert Path(references["preview_path"]).read_text(encoding="utf-8") == preview_svg
    assert Path(references["visual_preview_path"]).read_bytes() == preview_image
    assert canvas_store.load_canvas_preview(canvas_id) == ""
    assert canvas_store.load_canvas_visual_preview(canvas_id) == b""
    assert canvas_store.canvas_visual_preview_data_url(canvas_id) == ""
    assert saved["visual_preview_mime_type"] == "image/png"

    current_document = canvas_store.load_canvas_document(canvas_id)
    refreshed, preview_error = canvas_runtime._save_canvas_payload(
        canvas_id,
        current_document,
        "<svg>refreshed preview</svg>",
        "data:image/png;base64,aW1hZ2U=",
    )

    assert preview_error == ""
    assert refreshed["current_revision"] == 2
    assert refreshed["preview_content_hash"] == refreshed["content_hash"]
    assert refreshed["visual_preview_content_hash"] == refreshed["content_hash"]
    assert canvas_store.load_canvas_preview(canvas_id) == "<svg>refreshed preview</svg>"
    assert canvas_store.load_canvas_visual_preview(canvas_id) == b"image"


def test_empty_delayed_preview_clears_stale_preview(isolated_canvas_root):
    manifest = canvas_store.initialize_canvas("thread-empty-delayed-preview")
    canvas_id = manifest["canvas_id"]
    canvas_store.save_canvas_snapshot(
        canvas_id,
        {"store": {"shape:one": {"id": "shape:one", "typeName": "shape"}}},
        "<svg>old preview</svg>",
        b"old preview image",
    )
    empty_document = {"store": {}}

    canvas_store.save_canvas_snapshot(
        canvas_id,
        empty_document,
        None,
        None,
    )
    saved = canvas_store.save_canvas_snapshot(
        canvas_id,
        empty_document,
        "",
        b"",
    )

    references = canvas_store.canvas_file_references(canvas_id)
    assert saved["preview_content_hash"] == saved["content_hash"]
    assert saved["visual_preview_content_hash"] == saved["content_hash"]
    assert Path(references["preview_path"]).read_text(encoding="utf-8") == ""
    assert Path(references["visual_preview_path"]).read_bytes() == b""
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
    async def scenario() -> None:
        broker = CanvasBroker()
        first = broker.register("canvas-one")

        second = broker.register("canvas-one")

        assert first.retired.is_set()
        assert await first.outgoing.get() is None
        assert broker.is_current("canvas-one", first) is False
        assert broker.is_current("canvas-one", second) is True

    asyncio.run(scenario())


def test_canvas_broker_newer_same_owner_generation_replaces_with_retryable_code():
    async def scenario() -> None:
        broker = CanvasBroker()
        first = broker.register("canvas-one", "tab-one", 1)

        second = broker.register("canvas-one", "tab-one", 2)

        assert first.retired.is_set()
        assert (
            first.retired_close_code
            == canvas_runtime.CANVAS_SAME_OWNER_REPLACED_CLOSE_CODE
        )
        assert await first.outgoing.get() is None
        assert broker.is_current("canvas-one", second) is True

    asyncio.run(scenario())


def test_canvas_broker_replacement_immediately_fails_pending_request():
    async def scenario() -> None:
        broker = CanvasBroker()
        first = broker.register("canvas-one", "tab-one", 1)
        call = asyncio.create_task(
            asyncio.to_thread(broker.call, "canvas-one", "read_scene")
        )
        request = await first.outgoing.get()

        broker.register("canvas-one", "tab-one", 2)

        result = await asyncio.wait_for(call, timeout=1)
        assert request is not None
        assert result == {
            "ok": False,
            "error": "canvas_connection_replaced",
            "payload": {
                "error": "canvas_connection_replaced",
                "retryable": True,
                "message": (
                    "The Canvas connection changed before the command completed."
                ),
            },
        }

    asyncio.run(scenario())


def test_canvas_broker_older_same_owner_generation_cannot_replace_current():
    async def scenario() -> None:
        broker = CanvasBroker()
        current = broker.register("canvas-one", "tab-one", 2)

        stale = broker.register("canvas-one", "tab-one", 1)

        assert stale.retired.is_set()
        assert (
            stale.retired_close_code
            == canvas_runtime.CANVAS_SAME_OWNER_REPLACED_CLOSE_CODE
        )
        assert await stale.outgoing.get() is None
        assert broker.is_current("canvas-one", stale) is False
        assert broker.is_current("canvas-one", current) is True

    asyncio.run(scenario())


def test_canvas_broker_different_owner_replacement_remains_terminal():
    async def scenario() -> None:
        broker = CanvasBroker()
        first = broker.register("canvas-one", "tab-one", 1)

        second = broker.register("canvas-one", "tab-two", 1)

        assert first.retired.is_set()
        assert first.retired_close_code == canvas_runtime.CANVAS_REPLACED_CLOSE_CODE
        assert await first.outgoing.get() is None
        assert broker.is_current("canvas-one", second) is True

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "query_params",
    [
        {},
        {"owner_id": "page-one"},
        {"generation": "1"},
        {"owner_id": "invalid owner", "generation": "1"},
    ],
)
def test_canvas_websocket_accepts_before_invalid_identity_close(
    isolated_canvas_root, monkeypatch, query_params
):
    manifest = canvas_store.initialize_canvas("thread-invalid-identity")
    monkeypatch.setattr(canvas_runtime, "auth_required", lambda: False)

    class FakeWebSocket:
        scope = {}
        path_params = {"canvas_id": manifest["canvas_id"]}
        accepted = False
        close_code = 0

        def __init__(self):
            self.query_params = query_params

        async def accept(self):
            self.accepted = True

        async def close(self, code=1000):
            self.close_code = code

    websocket = FakeWebSocket()
    asyncio.run(canvas_runtime.canvas_websocket(websocket))

    assert websocket.accepted is True
    assert (
        websocket.close_code
        == canvas_runtime.CANVAS_INVALID_CONNECTION_CLOSE_CODE
    )


@pytest.mark.parametrize(
    ("auth_is_required", "canvas_is_present", "expected_close_code"),
    [
        (True, True, canvas_runtime.CANVAS_AUTH_REQUIRED_CLOSE_CODE),
        (False, False, canvas_runtime.CANVAS_NOT_FOUND_CLOSE_CODE),
    ],
)
def test_canvas_websocket_accepts_before_terminal_access_close(
    monkeypatch, auth_is_required, canvas_is_present, expected_close_code
):
    monkeypatch.setattr(canvas_runtime, "auth_required", lambda: auth_is_required)
    monkeypatch.setattr(
        canvas_runtime, "valid_auth_session_token", lambda _token: False
    )
    monkeypatch.setattr(
        canvas_runtime, "canvas_exists", lambda _canvas_id: canvas_is_present
    )

    class FakeWebSocket:
        scope = {}
        path_params = {"canvas_id": "canvas-one"}
        accepted = False
        close_code = 0

        async def accept(self):
            self.accepted = True

        async def close(self, code=1000):
            self.close_code = code

    websocket = FakeWebSocket()
    asyncio.run(canvas_runtime.canvas_websocket(websocket))

    assert websocket.accepted is True
    assert websocket.close_code == expected_close_code


def test_canvas_replacement_waits_for_active_response_commit(
    isolated_canvas_root, monkeypatch
):
    manifest = canvas_store.initialize_canvas("thread-replacement-drain")
    canvas_id = manifest["canvas_id"]
    broker = CanvasBroker()
    monkeypatch.setattr(canvas_runtime, "CANVAS_BROKER", broker)
    monkeypatch.setattr(canvas_runtime, "auth_required", lambda: False)
    save_started = threading.Event()
    allow_save = threading.Event()
    real_save = canvas_runtime._save_canvas_payload

    def blocking_save(*args, **kwargs):
        save_started.set()
        assert allow_save.wait(timeout=2)
        return real_save(*args, **kwargs)

    monkeypatch.setattr(canvas_runtime, "_save_canvas_payload", blocking_save)

    async def scenario() -> None:
        request_id = "replacement-drain-response"
        pending = canvas_runtime.PendingCanvasRequest(
            context={
                "command_id": "replacement-drain-command",
                "input_hash": "sha256:" + ("d" * 64),
                "base_revision": 0,
            }
        )
        response_ack = asyncio.Event()
        old_disconnect = asyncio.Event()
        new_accepted = asyncio.Event()
        new_registered = asyncio.Event()
        new_disconnect = asyncio.Event()

        class OldWebSocket:
            scope = {}
            path_params = {"canvas_id": canvas_id}
            query_params = {"owner_id": "old-page", "generation": "1"}
            receive_count = 0

            async def accept(self):
                return None

            async def close(self, code=1000):
                return None

            async def receive_json(self):
                if self.receive_count == 0:
                    self.receive_count += 1
                    with broker._lock:
                        connection = broker._connections[canvas_id]
                        pending.connection = connection
                        broker._pending[request_id] = pending
                    return {
                        "type": "response",
                        "id": request_id,
                        "ok": True,
                        "payload": {
                            "document": {
                                "store": {
                                    "shape:drained": {
                                        "id": "shape:drained",
                                        "typeName": "shape",
                                    }
                                }
                            },
                            "changed_ids": ["shape:drained"],
                            "refs": {},
                            "warnings": [],
                        },
                    }
                await old_disconnect.wait()
                raise WebSocketDisconnect()

            async def send_json(self, message):
                if message.get("type") == "response_ack":
                    response_ack.set()

        class NewWebSocket:
            scope = {}
            path_params = {"canvas_id": canvas_id}
            query_params = {"owner_id": "new-page", "generation": "1"}

            async def accept(self):
                new_accepted.set()

            async def close(self, code=1000):
                return None

            async def receive_json(self):
                new_registered.set()
                await new_disconnect.wait()
                raise WebSocketDisconnect()

            async def send_json(self, message):
                return None

        old_task = asyncio.create_task(
            canvas_runtime.canvas_websocket(OldWebSocket())
        )
        assert await asyncio.to_thread(save_started.wait, 1)
        with broker._lock:
            old_connection = broker._connections[canvas_id]

        new_task = asyncio.create_task(
            canvas_runtime.canvas_websocket(NewWebSocket())
        )
        await asyncio.wait_for(new_accepted.wait(), timeout=1)
        await asyncio.sleep(0)

        assert new_registered.is_set() is False
        assert broker.is_current(canvas_id, old_connection) is True

        allow_save.set()
        await asyncio.wait_for(response_ack.wait(), timeout=1)
        await asyncio.wait_for(new_registered.wait(), timeout=1)

        assert pending.result is not None
        assert pending.result["ok"] is True
        assert old_connection.retired.is_set()
        assert canvas_store.load_canvas_document(canvas_id) == {
            "store": {
                "shape:drained": {
                    "id": "shape:drained",
                    "typeName": "shape",
                }
            }
        }

        old_disconnect.set()
        new_disconnect.set()
        await asyncio.gather(old_task, new_task)

    asyncio.run(scenario())


def test_canvas_dynamic_tool_manifest_uses_namespace_shape():
    namespace = canvas_dynamic_tools()[0]

    assert namespace["type"] == "namespace"
    assert namespace["name"] == "canvas"
    assert "Nomad Surface embedded Canvas" in namespace["description"]
    assert [tool["name"] for tool in namespace["tools"]] == [
        "read_scene",
        "apply_patch",
        "export",
    ]
    assert "viewport" in namespace["tools"][0]["description"]
    read_schema = namespace["tools"][0]["inputSchema"]
    Draft202012Validator.check_schema(read_schema)
    scopes = read_schema["properties"]["scope"]["oneOf"]
    assert [item["properties"]["type"]["const"] for item in scopes] == [
        "all",
        "viewport",
        "selection",
        "bounds",
        "frame",
        "shape_ids",
    ]
    assert all(item["additionalProperties"] is False for item in scopes)
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
    operation_names = [
        item["properties"]["op"]["const"] for item in operation_schemas
    ]
    assert operation_names == [
        "create",
        "create_image",
        "draw",
        "draw",
        "draw",
        "update",
        "move",
        "resize",
        "delete",
        "connect",
        "disconnect",
        "group",
        "ungroup",
        "reparent",
        "reorder",
        "rotate",
        "flip",
        "align",
        "distribute",
        "stack",
        "pack",
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
    export_schema = namespace["tools"][2]["inputSchema"]
    Draft202012Validator.check_schema(export_schema)
    assert export_schema["required"] == ["format"]
    assert export_schema["properties"]["format"]["const"] == "obsidian"
    export_validator = Draft202012Validator(export_schema)
    assert list(export_validator.iter_errors({"format": "obsidian"})) == []
    assert list(export_validator.iter_errors({}))
    assert list(
        export_validator.iter_errors({"format": "obsidian", "path": "drawing.md"})
    )


def test_canvas_read_scene_schema_rejects_open_or_oversized_scopes():
    schema = canvas_runtime._canvas_read_scene_schema()
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    assert list(
        validator.iter_errors(
            {
                "scope": {
                    "type": "bounds",
                    "x": 0,
                    "y": 0,
                    "width": 100,
                    "height": 100,
                },
                "detail": "standard",
                "include_image": False,
                "max_image_dimension": 1024,
            }
        )
    ) == []
    assert list(
        validator.iter_errors({"scope": {"type": "all", "extra": True}})
    )
    assert list(
        validator.iter_errors(
            {
                "scope": {
                    "type": "shape_ids",
                    "ids": [f"shape:{index}" for index in range(101)],
                }
            }
        )
    )


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
                    "semantic_id": "gate.diagnosis-type",
                    "source_refs": [
                        {
                            "document": "requirements.md",
                            "locator": "section:3.2",
                            "content_hash": "sha256:" + ("a" * 64),
                        }
                    ],
                },
            },
            {
                "op": "connect",
                "ref": "edge",
                "from": {"ref": "gate"},
                "to": {"semantic_id": "outcome.approved"},
                "semantic_id": "flow.diagnosis-to-approved",
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
    invalid_semantic = {
        **valid,
        "operations": [
            {
                "op": "move",
                "target": {"semantic_id": "contains spaces"},
                "x": 1,
                "y": 2,
            }
        ],
    }
    assert list(validator.iter_errors(invalid_semantic))

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


def test_canvas_apply_patch_schema_accepts_drawing_and_layout_operations():
    validator = Draft202012Validator(canvas_runtime._canvas_apply_patch_schema())
    targets = [{"id": "shape:one"}, {"id": "shape:two"}]
    valid = {
        "command_id": "drawing-and-layout",
        "base_revision": 0,
        "operations": [
            {
                "op": "draw",
                "ref": "stroke",
                "kind": "freehand",
                "points": [
                    {"x": 100, "y": 100},
                    {"x": 120, "y": 110, "pressure": 0.7},
                ],
                "style": {"color": "red", "size": "m"},
            },
            {
                "op": "draw",
                "ref": "highlight",
                "kind": "highlight",
                "points": [{"x": 80, "y": 90}, {"x": 140, "y": 90}],
                "style": {"color": "yellow", "size": "l"},
            },
            {
                "op": "draw",
                "ref": "line",
                "kind": "line",
                "points": [{"x": 0, "y": 0}, {"x": 50, "y": 20}],
                "spline": "cubic",
                "style": {"color": "blue", "dash": "solid"},
            },
            {"op": "group", "ref": "group", "targets": targets},
            {"op": "ungroup", "targets": [{"ref": "group"}]},
            {"op": "disconnect", "target": {"id": "shape:arrow"}},
            {"op": "reparent", "targets": targets, "parent": None},
            {"op": "reorder", "targets": targets, "position": "front"},
            {"op": "rotate", "targets": targets, "degrees": 45},
            {"op": "flip", "targets": targets, "axis": "horizontal"},
            {"op": "align", "targets": targets, "alignment": "left"},
            {
                "op": "distribute",
                "targets": [*targets, {"id": "shape:three"}],
                "axis": "horizontal",
            },
            {"op": "stack", "targets": targets, "axis": "vertical", "gap": 16},
            {"op": "pack", "targets": targets, "gap": 8},
        ],
    }
    raw_draw = {
        "command_id": "raw-draw",
        "base_revision": 0,
        "operations": [
            {
                "op": "create",
                "ref": "stroke",
                "shape": {
                    "type": "draw",
                    "x": 0,
                    "y": 0,
                    "props": {"segments": []},
                },
            }
        ],
    }

    assert list(validator.iter_errors(valid)) == []
    assert list(validator.iter_errors(raw_draw))


def test_offline_semantic_summary_is_bounded():
    summary = canvas_runtime._offline_semantic_summary(
        {
            "nomad": {
                "semantic_id": "invalid semantic id",
                "created_by": "x" * 65,
                "last_command_id": "x" * 129,
                "source_refs": [
                    {"document": "requirements.md", "locator": "section:1"},
                    {"document": "x" * 501, "locator": "section:oversized"},
                ],
            }
        }
    )

    assert "semantic_id" not in summary
    assert "created_by" not in summary
    assert "last_command_id" not in summary
    assert summary["source_refs"] == [
        {"document": "requirements.md", "locator": "section:1"}
    ]


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


def test_canvas_command_receipt_round_trips_structured_lint(isolated_canvas_root):
    manifest = canvas_store.initialize_canvas("thread-structured-lint")
    canvas_id = manifest["canvas_id"]
    warning = {
        "code": "missing_semantic_id",
        "severity": "warning",
        "shape_ids": ["shape:gate"],
        "semantic_ids": [],
        "message": "A shape with provenance has no semantic ID.",
        "details": {},
        "suggested_action": "Assign a document-unique semantic_id.",
    }
    canvas_store.save_canvas_snapshot(
        canvas_id,
        {"store": {}},
        command_receipt={
            "command_id": "structured-lint",
            "input_hash": "sha256:" + ("a" * 64),
            "base_revision": 0,
            "changed_ids": ["shape:gate"],
            "refs": {},
            "warnings": [warning],
        },
    )

    receipt = canvas_store.read_canvas_command_receipt(canvas_id, "structured-lint")
    assert receipt["schema_version"] == 2
    assert receipt["warnings"] == [warning]
    assert canvas_runtime._receipt_payload(canvas_id, receipt)["semantic_success"] is True


def test_canvas_command_receipt_schema_versions_keep_warning_formats_distinct():
    base_receipt = {
        "command_id": "warning-schema",
        "input_hash": "sha256:" + ("a" * 64),
        "base_revision": 0,
        "result_revision": 1,
        "changed_ids": [],
        "refs": {},
        "committed_at": "2026-08-21T00:00:00+00:00",
    }
    legacy = {
        **base_receipt,
        "schema_version": 1,
        "warnings": ["Legacy warning"],
    }
    structured_warning = {
        "code": "missing_semantic_id",
        "severity": "warning",
        "shape_ids": ["shape:gate"],
        "semantic_ids": [],
        "message": "A shape with provenance has no semantic ID.",
        "details": {},
        "suggested_action": "Assign a document-unique semantic_id.",
    }
    current = {
        **base_receipt,
        "schema_version": 2,
        "warnings": [structured_warning],
    }

    assert canvas_store._validate_command_receipt(legacy) == legacy
    assert canvas_store._validate_command_receipt(current) == current
    with pytest.raises(canvas_store.CanvasCommandReceiptError):
        canvas_store._validate_command_receipt(
            {**legacy, "warnings": [structured_warning]}
        )
    with pytest.raises(canvas_store.CanvasCommandReceiptError):
        canvas_store._validate_command_receipt(
            {**current, "warnings": ["Legacy warning"]}
        )


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
    assert saved["preview_content_hash"] != saved["content_hash"]
    assert saved["visual_preview_content_hash"] != saved["content_hash"]
    assert canvas_store.load_canvas_preview(canvas_id) == ""
    assert canvas_store.canvas_visual_preview_data_url(canvas_id) == ""
    assert canvas_store.read_canvas_command_receipt(
        canvas_id, "committed-without-cache"
    )["result_revision"] == 1


def test_preview_manifest_failure_keeps_committed_preview_unpublished(
    isolated_canvas_root, monkeypatch
):
    manifest = canvas_store.initialize_canvas("thread-preview-manifest-failure")
    canvas_id = manifest["canvas_id"]
    original_atomic_write = canvas_store._atomic_write
    manifest_writes = 0

    def fail_preview_manifest(path, content):
        nonlocal manifest_writes
        if path.name == "manifest.json":
            manifest_writes += 1
            if manifest_writes == 2:
                raise OSError("preview manifest update failed")
        original_atomic_write(path, content)

    monkeypatch.setattr(canvas_store, "_atomic_write", fail_preview_manifest)

    saved = canvas_store.save_canvas_snapshot(
        canvas_id,
        {"store": {"shape:saved": {"id": "shape:saved", "typeName": "shape"}}},
        "<svg>saved</svg>",
        b"saved image",
    )

    persisted = canvas_store.read_canvas_manifest(canvas_id)
    assert saved["current_revision"] == 1
    assert persisted == saved
    assert saved["preview_content_hash"] != saved["content_hash"]
    assert saved["visual_preview_content_hash"] != saved["content_hash"]
    assert canvas_store.load_canvas_preview(canvas_id) == ""
    assert canvas_store.canvas_visual_preview_data_url(canvas_id) == ""


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
            query_params = {"owner_id": "invalid-response", "generation": "1"}
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


def test_export_response_is_saved_without_returning_markdown(
    isolated_canvas_root, monkeypatch
):
    manifest = canvas_store.initialize_canvas("thread-export-response")
    canvas_id = manifest["canvas_id"]
    markdown = _obsidian_markdown()
    broker = CanvasBroker()
    monkeypatch.setattr(canvas_runtime, "CANVAS_BROKER", broker)
    monkeypatch.setattr(canvas_runtime, "auth_required", lambda: False)

    async def exercise_websocket():
        request_id = "export-response"
        pending = canvas_runtime.PendingCanvasRequest(
            context={"method": "export"}
        )

        class FakeWebSocket:
            scope = {}
            path_params = {"canvas_id": canvas_id}
            query_params = {"owner_id": "export-response", "generation": "1"}
            receive_count = 0

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
                        "payload": {
                            "format": "obsidian",
                            "markdown": markdown,
                        },
                    }
                raise WebSocketDisconnect()

            async def send_json(self, message):
                return None

        await canvas_runtime.canvas_websocket(FakeWebSocket())
        return pending.result

    result = asyncio.run(exercise_websocket())
    payload = result["payload"]

    assert result["ok"] is True
    assert payload["live"] is True
    assert payload["format"] == "obsidian"
    assert "markdown" not in payload
    assert Path(payload["export_path"]).read_text(encoding="utf-8") == markdown


def test_successful_apply_result_excludes_document_scene_and_images(
    isolated_canvas_root, monkeypatch
):
    manifest = canvas_store.initialize_canvas("thread-compact-apply")
    canvas_id = manifest["canvas_id"]
    broker = CanvasBroker()
    monkeypatch.setattr(canvas_runtime, "CANVAS_BROKER", broker)
    monkeypatch.setattr(canvas_runtime, "auth_required", lambda: False)

    async def exercise_websocket():
        request_id = "compact-apply-response"
        pending = canvas_runtime.PendingCanvasRequest(
            context={
                "command_id": "compact-command",
                "input_hash": "sha256:" + ("a" * 64),
                "base_revision": 0,
            }
        )
        ack_received = asyncio.Event()

        class FakeWebSocket:
            scope = {}
            path_params = {"canvas_id": canvas_id}
            query_params = {"owner_id": "compact-response", "generation": "1"}
            receive_count = 0

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
                        "payload": {
                            "document": {
                                "store": {
                                    "shape:one": {
                                        "id": "shape:one",
                                        "typeName": "shape",
                                    }
                                }
                            },
                            "scene": {"shapes": [{"id": "shape:one"}]},
                            "preview_svg": "<svg />",
                            "preview_image_url": "data:image/webp;base64,UklGRg==",
                            "changed_ids": ["shape:one"],
                            "refs": {"one": "shape:one"},
                            "warnings": [],
                        },
                    }
                await asyncio.wait_for(ack_received.wait(), timeout=1)
                raise WebSocketDisconnect()

            async def send_json(self, message):
                if message.get("type") == "response_ack":
                    ack_received.set()

        await canvas_runtime.canvas_websocket(FakeWebSocket())
        return pending.result

    result = asyncio.run(exercise_websocket())
    payload = result["payload"]

    assert result["ok"] is True
    assert payload["revision"] == 1
    assert payload["changed_ids"] == ["shape:one"]
    assert payload["refs"] == {"one": "shape:one"}
    assert "document" not in payload
    assert "scene" not in payload
    assert "preview_svg" not in payload
    assert "preview_image_url" not in payload


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
    assert "narrowest useful" in items[0]["content"][0]["text"]
    assert "current schemas as authoritative" in items[0]["content"][0]["text"]
    assert "preserving unrelated user content" in items[0]["content"][0]["text"]
    assert "external or offline canvas integrations" in items[0]["content"][0][
        "text"
    ]
    assert "Export only when the user asks" in items[0]["content"][0]["text"]
