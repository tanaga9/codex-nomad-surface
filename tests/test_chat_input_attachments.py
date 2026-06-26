from streamlit.elements.widgets.chat import ChatInputValue

from codex_nomad_surface.app import (
    chat_input_files,
    chat_input_text,
    cleanup_uploaded_chat_images,
    remember_uploaded_chat_image_cleanup,
    prompt_with_local_image_references,
)


def test_chat_input_value_files_are_extracted() -> None:
    value = ChatInputValue(text="Describe it", files=["image"], _include_files=True)

    assert chat_input_text(value) == "Describe it"
    assert chat_input_files(value) == ["image"]


def test_chat_input_mapping_files_are_extracted() -> None:
    value = {"text": "Describe it", "files": ["image"]}

    assert chat_input_text(value) == "Describe it"
    assert chat_input_files(value) == ["image"]


def test_prompt_with_local_image_references_matches_codex_attachment_style() -> None:
    prompt = prompt_with_local_image_references(
        "Describe it",
        [{"name": "example.png"}],
        ["/tmp/example.png"],
    )

    assert "Files mentioned by the user:" in prompt
    assert "- example.png: /tmp/example.png" in prompt
    assert "My request for Codex:" in prompt
    assert prompt.endswith("Describe it")


def test_cleanup_uploaded_chat_images_removes_only_upload_temp_files(
    monkeypatch, tmp_path
) -> None:
    import codex_nomad_surface.app as app

    upload_root = tmp_path / "uploads"
    monkeypatch.setattr(app, "CHAT_INPUT_IMAGE_TEMP_DIR", upload_root)
    image_dir = upload_root / "run-1"
    image_dir.mkdir(parents=True)
    image_path = image_dir / "01-image.png"
    image_path.write_bytes(b"image")
    outside_path = tmp_path / "outside.png"
    outside_path.write_bytes(b"outside")

    cleanup_uploaded_chat_images([str(image_path), str(outside_path)])

    assert not image_path.exists()
    assert not image_dir.exists()
    assert outside_path.exists()


def test_cleanup_uploaded_chat_images_ignores_unlink_failures(
    monkeypatch, tmp_path
) -> None:
    import codex_nomad_surface.app as app

    upload_root = tmp_path / "uploads"
    monkeypatch.setattr(app, "CHAT_INPUT_IMAGE_TEMP_DIR", upload_root)
    image_dir = upload_root / "run-1"
    image_dir.mkdir(parents=True)
    image_path = image_dir / "01-image.png"
    image_path.write_bytes(b"image")

    def fail_unlink(self, missing_ok=False):  # noqa: ANN001
        raise OSError("locked")

    monkeypatch.setattr(app.Path, "unlink", fail_unlink)

    cleanup_uploaded_chat_images([str(image_path)])

    assert image_path.exists()


def test_remember_uploaded_chat_image_cleanup_deduplicates_paths() -> None:
    pending = {"local_image_cleanup_paths": ["/tmp/a.png"]}

    remember_uploaded_chat_image_cleanup(pending, ["/tmp/a.png", "/tmp/b.png"])

    assert pending["local_image_cleanup_paths"] == ["/tmp/a.png", "/tmp/b.png"]
