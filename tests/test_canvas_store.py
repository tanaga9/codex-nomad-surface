import json
from pathlib import Path

import pytest

from codex_nomad_surface import canvas_store
from codex_nomad_surface.canvas_runtime import (
    canvas_dynamic_tool_handler,
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

    saved = canvas_store.save_canvas_snapshot(canvas_id, document, "<svg />")
    references = canvas_store.canvas_file_references(canvas_id)

    assert saved["current_revision"] == 1
    assert saved["project_path"] == "/path/to/project"
    assert canvas_store.load_canvas_document(canvas_id) == document
    assert canvas_store.load_canvas_preview(canvas_id) == "<svg />"
    assert json.loads(Path(references["document_path"]).read_text(encoding="utf-8")) == document
    assert Path(references["preview_path"]).read_text(encoding="utf-8") == "<svg />"
    assert (
        isolated_canvas_root
        / canvas_id
        / "revisions"
        / "00000001"
        / "document.json"
    ).is_file()
    assert canvas_store.list_canvas_manifests()[0]["thread_id"] == "thread-file-store"


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


def test_empty_canvas_clears_saved_preview(isolated_canvas_root):
    manifest = canvas_store.initialize_canvas("thread-preview-clear")
    canvas_id = manifest["canvas_id"]
    canvas_store.save_canvas_snapshot(
        canvas_id,
        {"store": {"shape:one": {"id": "shape:one", "typeName": "shape"}}},
        "<svg>old preview</svg>",
    )

    canvas_store.save_canvas_snapshot(canvas_id, {"store": {}}, "")

    assert canvas_store.load_canvas_preview(canvas_id) == ""


def test_canvas_dynamic_tool_manifest_uses_namespace_shape():
    namespace = canvas_dynamic_tools()[0]

    assert namespace["type"] == "namespace"
    assert namespace["name"] == "canvas"
    assert [tool["name"] for tool in namespace["tools"]] == [
        "read_scene",
        "apply_patch",
    ]
    apply_schema = namespace["tools"][1]["inputSchema"]
    assert apply_schema["required"] == ["command_id", "base_revision", "operations"]
