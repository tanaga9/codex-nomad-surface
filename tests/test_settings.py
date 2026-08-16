from pathlib import Path

import pytest

from codex_nomad_surface import settings
from codex_nomad_surface.settings import (
    AppSettings,
    DEFAULT_CANVAS_CHAT_HISTORY_MESSAGE_LIMIT,
    MAX_CANVAS_CHAT_HISTORY_MESSAGE_LIMIT,
    auth_dummy_username_field_enabled,
    auth_session_days,
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
            canvas_chat_history_message_limit=3,
        )
    )

    loaded = load_settings()

    assert loaded.app_server_url == "ws://127.0.0.1:9999"
    assert loaded.new_chat_model_provider == "openai"
    assert loaded.new_chat_model == "gpt-test"
    assert loaded.new_chat_reasoning_effort == "high"
    assert loaded.canvas_chat_history_message_limit == 3


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (None, DEFAULT_CANVAS_CHAT_HISTORY_MESSAGE_LIMIT),
        ("invalid", DEFAULT_CANVAS_CHAT_HISTORY_MESSAGE_LIMIT),
        (-1, 0),
        (999, MAX_CANVAS_CHAT_HISTORY_MESSAGE_LIMIT),
    ],
)
def test_canvas_chat_history_message_limit_is_bounded(
    raw_value: object, expected: int
) -> None:
    raw = {}
    if raw_value is not None:
        raw["canvas_chat_history_message_limit"] = raw_value

    assert AppSettings.from_dict(raw).canvas_chat_history_message_limit == expected


def test_auth_dummy_username_field_env_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NOMAD_AUTH_DUMMY_USERNAME_FIELD", raising=False)
    assert not auth_dummy_username_field_enabled()

    monkeypatch.setenv("NOMAD_AUTH_DUMMY_USERNAME_FIELD", "true")
    assert auth_dummy_username_field_enabled()

    monkeypatch.setenv("NOMAD_AUTH_DUMMY_USERNAME_FIELD", "0")
    assert not auth_dummy_username_field_enabled()


def test_auth_session_days_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOMAD_AUTH_SESSION_DAYS", raising=False)
    assert auth_session_days() == 180

    monkeypatch.setenv("NOMAD_AUTH_SESSION_DAYS", "0")
    assert auth_session_days() == 1

    monkeypatch.setenv("NOMAD_AUTH_SESSION_DAYS", "999")
    assert auth_session_days() == 365

    monkeypatch.setenv("NOMAD_AUTH_SESSION_DAYS", "not-a-number")
    assert auth_session_days() == 180
