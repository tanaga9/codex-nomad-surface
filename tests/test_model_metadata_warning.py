from codex_nomad_surface.app import model_metadata_warning


def test_model_metadata_warning_ignores_openai_provider() -> None:
    warning = model_metadata_warning(
        "openai",
        "gpt-5.5",
        [{"id": "gpt-5.5"}],
    )

    assert warning == ""


def test_model_metadata_warning_ignores_models_returned_by_app_server() -> None:
    warning = model_metadata_warning(
        "local_ollama",
        "gemma4:12b",
        [{"id": "gemma4:12b"}],
    )

    assert warning == ""


def test_model_metadata_warning_flags_provider_model_without_metadata() -> None:
    warning = model_metadata_warning(
        "local_ollama",
        "gemma4:12b",
        [{"id": "gpt-5.5"}],
    )

    assert "gemma4:12b" in warning
    assert "metadata" in warning
    assert "empty response" in warning


def test_model_metadata_warning_distinguishes_model_list_errors() -> None:
    warning = model_metadata_warning(
        "local_ollama",
        "gemma4:12b",
        [],
        "connection failed",
    )

    assert "Could not verify" in warning
    assert "gemma4:12b" in warning
