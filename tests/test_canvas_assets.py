import asyncio
import hashlib
import io
import json
import os
import threading
from pathlib import Path

import pytest
from PIL import Image
from starlette.requests import Request

from codex_nomad_surface import canvas_assets, canvas_runtime, canvas_store


@pytest.fixture
def isolated_canvas(tmp_path, monkeypatch):
    monkeypatch.setattr(canvas_store, "CANVAS_ROOT", tmp_path / "canvases")
    project = tmp_path / "project"
    project.mkdir()
    manifest = canvas_store.initialize_canvas("thread-assets", str(project))
    return manifest["canvas_id"], project


def _image_bytes(
    size: tuple[int, int] = (320, 180),
    *,
    image_format: str = "PNG",
) -> bytes:
    image = Image.new("RGB", size, (32, 120, 210))
    output = io.BytesIO()
    image.save(output, format=image_format)
    return output.getvalue()


def _document_with_image_asset(canvas_id: str, filename: str) -> dict:
    asset_id = f"asset:{filename.removesuffix('.webp')}"
    return {
        "store": {
            asset_id: {
                "id": asset_id,
                "typeName": "asset",
                "type": "image",
                "props": {
                    "src": f"/api/canvas/{canvas_id}/assets/{filename}",
                },
            },
            "shape:image": {
                "id": "shape:image",
                "typeName": "shape",
                "type": "image",
                "props": {"assetId": asset_id},
            },
        }
    }


def test_canvas_image_is_resized_and_stored_as_bounded_webp(isolated_canvas):
    canvas_id, _project = isolated_canvas
    source = _image_bytes((3_000, 1_500), image_format="JPEG")

    result = canvas_assets.normalize_and_save_canvas_image(
        canvas_id, source, "large-photo.jpg"
    )
    path = canvas_store.canvas_asset_path(canvas_id, result["filename"])

    assert result["mime_type"] == "image/webp"
    assert result["name"] == "large-photo.webp"
    assert max(result["width"], result["height"]) <= 2_048
    assert result["byte_size"] <= 768 * 1024
    assert path.read_bytes().startswith(b"RIFF")
    with Image.open(path) as optimized:
        assert optimized.format == "WEBP"
        assert optimized.size == (result["width"], result["height"])


def test_canvas_image_limits_fail_without_writing_asset(isolated_canvas, monkeypatch):
    canvas_id, _project = isolated_canvas
    monkeypatch.setattr(canvas_assets, "CANVAS_IMAGE_MAX_PIXELS", 100)

    with pytest.raises(canvas_assets.CanvasImageError) as captured:
        canvas_assets.normalize_and_save_canvas_image(
            canvas_id, _image_bytes((11, 10)), "too-many-pixels.png"
        )

    assert captured.value.code == "image_dimensions_too_large"
    assert not (canvas_store.canvas_directory(canvas_id) / "assets").exists()


def test_canvas_image_fails_when_quality_floor_cannot_meet_size_limit(
    isolated_canvas, monkeypatch
):
    canvas_id, _project = isolated_canvas
    monkeypatch.setattr(canvas_assets, "CANVAS_IMAGE_OPTIMIZED_MAX_BYTES", 1)

    with pytest.raises(canvas_assets.CanvasImageError) as captured:
        canvas_assets.normalize_and_save_canvas_image(
            canvas_id, _image_bytes((320, 180)), "cannot-fit.png"
        )

    assert captured.value.code == "image_optimization_failed"
    assert not (canvas_store.canvas_directory(canvas_id) / "assets").exists()


def test_canvas_asset_storage_deduplicates_content(isolated_canvas):
    canvas_id, _project = isolated_canvas
    content = b"RIFF-test-WEBP"
    digest = hashlib.sha256(content).hexdigest()

    first = canvas_store.save_canvas_asset(canvas_id, content, digest, "webp")
    second = canvas_store.save_canvas_asset(canvas_id, content, digest, "webp")

    assert first["deduplicated"] is False
    assert second["deduplicated"] is True
    assert len(list((canvas_store.canvas_directory(canvas_id) / "assets").iterdir())) == 1


def test_canvas_asset_quota_reclaims_stale_unreferenced_files(
    isolated_canvas, monkeypatch
):
    canvas_id, _project = isolated_canvas
    monkeypatch.setattr(canvas_store, "CANVAS_ASSET_MAX_COUNT", 1)
    first_content = b"first-webp"
    first_hash = hashlib.sha256(first_content).hexdigest()
    first = canvas_store.save_canvas_asset(
        canvas_id, first_content, first_hash, "webp"
    )
    first_path = canvas_store.canvas_asset_path(canvas_id, first["filename"])
    old_time = first_path.stat().st_mtime - (
        canvas_store.CANVAS_ASSET_PENDING_GRACE_SECONDS + 1
    )
    os.utime(first_path, (old_time, old_time))

    second_content = b"second-webp"
    second_hash = hashlib.sha256(second_content).hexdigest()
    second = canvas_store.save_canvas_asset(
        canvas_id, second_content, second_hash, "webp"
    )

    assert not first_path.exists()
    assert canvas_store.canvas_asset_path(canvas_id, second["filename"]).is_file()


def test_canvas_asset_cleanup_preserves_retained_revision_references(
    isolated_canvas, monkeypatch
):
    canvas_id, _project = isolated_canvas
    monkeypatch.setattr(canvas_store, "CANVAS_REVISION_LIMIT", 2)
    content = b"retained-webp"
    digest = hashlib.sha256(content).hexdigest()
    stored = canvas_store.save_canvas_asset(canvas_id, content, digest, "webp")
    path = canvas_store.canvas_asset_path(canvas_id, stored["filename"])
    canvas_store.save_canvas_snapshot(
        canvas_id, _document_with_image_asset(canvas_id, stored["filename"])
    )
    old_time = path.stat().st_mtime - (
        canvas_store.CANVAS_ASSET_PENDING_GRACE_SECONDS + 1
    )
    os.utime(path, (old_time, old_time))

    canvas_store.save_canvas_snapshot(
        canvas_id, {"store": {"shape:revision-2": {"id": "shape:revision-2"}}}
    )
    assert path.is_file()

    canvas_store.save_canvas_snapshot(
        canvas_id, {"store": {"shape:revision-3": {"id": "shape:revision-3"}}}
    )
    canvas_store.prune_canvas_assets(canvas_id)
    assert not path.exists()


def test_canvas_asset_cleanup_fails_closed_for_corrupt_retained_document(
    isolated_canvas,
):
    canvas_id, _project = isolated_canvas
    content = b"retained-after-corruption"
    digest = hashlib.sha256(content).hexdigest()
    stored = canvas_store.save_canvas_asset(canvas_id, content, digest, "webp")
    path = canvas_store.canvas_asset_path(canvas_id, stored["filename"])
    canvas_store.save_canvas_snapshot(
        canvas_id, _document_with_image_asset(canvas_id, stored["filename"])
    )
    path_to_document = (
        canvas_store.canvas_directory(canvas_id)
        / "revisions"
        / "00000001"
        / "document.json"
    )
    path_to_document.write_text("{not-json", encoding="utf-8")
    old_time = path.stat().st_mtime - (
        canvas_store.CANVAS_ASSET_PENDING_GRACE_SECONDS + 1
    )
    os.utime(path, (old_time, old_time))

    assert canvas_store.prune_canvas_assets(canvas_id) == 0
    assert path.is_file()


def test_canvas_snapshot_does_not_scan_assets_after_commit(
    isolated_canvas, monkeypatch
):
    canvas_id, _project = isolated_canvas

    def unexpected_cleanup(*_args, **_kwargs):
        raise AssertionError("snapshot commits must not scan retained asset references")

    monkeypatch.setattr(
        canvas_store, "_prune_canvas_assets_locked", unexpected_cleanup
    )

    manifest = canvas_store.save_canvas_snapshot(
        canvas_id, {"store": {"shape:new": {"id": "shape:new"}}}
    )

    assert manifest["current_revision"] == 1


def test_codex_image_import_stays_inside_project_and_uses_shared_optimizer(
    isolated_canvas,
):
    canvas_id, project = isolated_canvas
    image_path = project / "generated.png"
    image_path.write_bytes(_image_bytes((640, 360)))
    arguments = {
        "command_id": "add-generated-image",
        "base_revision": 0,
        "operations": [
            {
                "op": "create_image",
                "ref": "hero",
                "path": str(image_path),
                "x": 10,
                "y": 20,
                "alt_text": "Generated hero image",
            }
        ],
    }

    prepared = canvas_runtime._prepare_canvas_image_operations(canvas_id, arguments)
    shape = prepared["operations"][0]["shape"]

    assert "path" not in shape
    assert shape["asset"]["mime_type"] == "image/webp"
    assert shape["asset"]["byte_size"] <= 768 * 1024
    assert arguments["operations"][0]["path"] == str(image_path)

    outside = project.parent / "outside.png"
    outside.write_bytes(_image_bytes())
    arguments["operations"][0]["path"] = str(outside)
    with pytest.raises(canvas_assets.CanvasImageError) as captured:
        canvas_runtime._prepare_canvas_image_operations(canvas_id, arguments)
    assert captured.value.code == "image_path_not_allowed"


def test_http_upload_rejects_declared_oversize_before_reading_body(
    isolated_canvas, monkeypatch,
):
    canvas_id, _project = isolated_canvas
    monkeypatch.setattr(canvas_runtime, "auth_required", lambda: False)
    receive_called = False

    async def receive():
        nonlocal receive_called
        receive_called = True
        return {"type": "http.request", "body": b"unused", "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": f"/api/canvas/{canvas_id}/assets",
            "path_params": {"canvas_id": canvas_id},
            "headers": [
                (b"content-type", b"image/png"),
                (
                    b"content-length",
                    str(canvas_assets.CANVAS_IMAGE_SOURCE_MAX_BYTES + 1).encode(),
                ),
            ],
        },
        receive,
    )

    response = asyncio.run(canvas_runtime.canvas_asset_upload(request))
    payload = json.loads(response.body)

    assert response.status_code == 413
    assert payload["error"] == "image_source_too_large"
    assert receive_called is False


def test_http_upload_rejects_busy_processor_before_reading_body(
    isolated_canvas, monkeypatch
):
    canvas_id, _project = isolated_canvas
    monkeypatch.setattr(canvas_runtime, "auth_required", lambda: False)
    upload_slots = threading.BoundedSemaphore(1)
    assert upload_slots.acquire(blocking=False)
    monkeypatch.setattr(canvas_runtime, "_CANVAS_ASSET_UPLOAD_SLOTS", upload_slots)
    receive_called = False

    async def receive():
        nonlocal receive_called
        receive_called = True
        return {"type": "http.request", "body": b"unused", "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": f"/api/canvas/{canvas_id}/assets",
            "path_params": {"canvas_id": canvas_id},
            "headers": [
                (b"content-type", b"image/png"),
                (b"content-length", b"100"),
            ],
        },
        receive,
    )

    response = asyncio.run(canvas_runtime.canvas_asset_upload(request))
    payload = json.loads(response.body)

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert payload["error"] == "image_processing_busy"
    assert receive_called is False


def test_canvas_asset_response_disables_browser_storage(
    isolated_canvas, monkeypatch
):
    canvas_id, _project = isolated_canvas
    monkeypatch.setattr(canvas_runtime, "auth_required", lambda: False)
    content = b"cached-webp"
    digest = hashlib.sha256(content).hexdigest()
    stored = canvas_store.save_canvas_asset(canvas_id, content, digest, "webp")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": f"/api/canvas/{canvas_id}/assets/{stored['filename']}",
            "path_params": {
                "canvas_id": canvas_id,
                "filename": stored["filename"],
            },
            "headers": [],
        }
    )

    response = asyncio.run(canvas_runtime.canvas_asset_content(request))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"


def test_dynamic_tool_schema_exposes_bounded_image_create():
    namespace = canvas_runtime.canvas_dynamic_tools()[0]
    apply_tool = next(tool for tool in namespace["tools"] if tool["name"] == "apply_patch")
    operation_variants = apply_tool["inputSchema"]["properties"]["operations"]["items"]["oneOf"]
    image_variant = next(
        operation
        for operation in operation_variants
        if operation["properties"]["op"].get("const") == "create_image"
    )

    assert image_variant["required"] == ["op", "ref", "path", "x", "y"]
    assert image_variant["additionalProperties"] is False


def test_codex_relative_image_path_is_resolved_from_canvas_project(isolated_canvas):
    canvas_id, project = isolated_canvas
    image_path = project / "images" / "relative.png"
    image_path.parent.mkdir()
    image_path.write_bytes(_image_bytes())

    prepared = canvas_runtime._prepare_canvas_image_operations(
        canvas_id,
        {
            "command_id": "relative-image",
            "base_revision": 0,
            "operations": [
                {
                    "op": "create_image",
                    "ref": "relative",
                    "path": "images/relative.png",
                    "x": 0,
                    "y": 0,
                }
            ],
        },
    )

    assert prepared["operations"][0]["op"] == "create"
    assert prepared["operations"][0]["shape"]["asset"]["mime_type"] == "image/webp"


def test_dynamic_handler_sends_prepared_image_but_hashes_public_operation(
    isolated_canvas, monkeypatch
):
    canvas_id, project = isolated_canvas
    image_path = project / "handler.png"
    image_path.write_bytes(_image_bytes())
    captured = {}

    def fake_call(canvas_id_arg, method, arguments, **kwargs):
        captured.update(
            canvas_id=canvas_id_arg,
            method=method,
            arguments=arguments,
            status_arguments=kwargs.get("status_arguments"),
        )
        return {"ok": False, "error": "test_stop"}

    monkeypatch.setattr(canvas_runtime.CANVAS_BROKER, "call", fake_call)
    public_arguments = {
        "command_id": "handler-image",
        "base_revision": 0,
        "operations": [
            {
                "op": "create_image",
                "ref": "handler-image",
                "path": "handler.png",
                "x": 10,
                "y": 20,
            }
        ],
    }

    result = canvas_runtime.canvas_dynamic_tool_handler_for_canvas(canvas_id)(
        {
            "namespace": "canvas",
            "tool": "apply_patch",
            "arguments": public_arguments,
        }
    )

    assert result["success"] is False
    assert captured["arguments"]["operations"][0]["op"] == "create"
    assert captured["status_arguments"] == public_arguments


def test_create_image_rejects_raw_tldraw_fields(isolated_canvas):
    canvas_id, project = isolated_canvas
    image_path = project / "raw.png"
    image_path.write_bytes(_image_bytes())

    with pytest.raises(canvas_assets.CanvasImageError) as captured:
        canvas_runtime._prepare_canvas_image_operations(
            canvas_id,
            {
                "operations": [
                    {
                        "op": "create_image",
                        "ref": "raw",
                        "path": "raw.png",
                        "x": 0,
                        "y": 0,
                        "props": {"assetId": "asset:raw"},
                    }
                ]
            },
        )

    assert captured.value.code == "image_operation_invalid"
