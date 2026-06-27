from pathlib import Path

import pytest

from codex_nomad_surface import settings
from codex_nomad_surface.settings import (
    AppSettings,
    auth_dummy_username_field_enabled,
    load_settings,
    save_settings,
)


def test_new_chat_runtime_defaults_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "APP_DIR", tmp_path)
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")

    save_settings(
        AppSettings(
            app_server_url="ws://127.0.0.1:9999",
            new_chat_model_provider="openai",
            new_chat_model="gpt-test",
            new_chat_reasoning_effort="high",
        )
    )

    loaded = load_settings()

    assert loaded.app_server_url == "ws://127.0.0.1:9999"
    assert loaded.new_chat_model_provider == "openai"
    assert loaded.new_chat_model == "gpt-test"
    assert loaded.new_chat_reasoning_effort == "high"


def test_auth_dummy_username_field_env_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NOMAD_AUTH_DUMMY_USERNAME_FIELD", raising=False)
    assert not auth_dummy_username_field_enabled()

    monkeypatch.setenv("NOMAD_AUTH_DUMMY_USERNAME_FIELD", "true")
    assert auth_dummy_username_field_enabled()

    monkeypatch.setenv("NOMAD_AUTH_DUMMY_USERNAME_FIELD", "0")
    assert not auth_dummy_username_field_enabled()
