from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


APP_DIR = Path(".nomad_surface")
SETTINGS_PATH = APP_DIR / "settings.json"
DEFAULT_APP_SERVER_URL = "ws://127.0.0.1:8080"


@dataclass
class Project:
    name: str
    path: str


@dataclass
class AppSettings:
    app_server_url: str = DEFAULT_APP_SERVER_URL
    new_chat_model_provider: str = ""
    new_chat_model: str = ""
    new_chat_reasoning_effort: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AppSettings":
        app_server_url = raw.get("app_server_url") or cls.app_server_url
        return cls(
            app_server_url=app_server_url,
            new_chat_model_provider=str(raw.get("new_chat_model_provider") or ""),
            new_chat_model=str(raw.get("new_chat_model") or ""),
            new_chat_reasoning_effort=str(
                raw.get("new_chat_reasoning_effort") or ""
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_settings() -> AppSettings:
    if not SETTINGS_PATH.exists():
        return AppSettings()
    try:
        return AppSettings.from_dict(
            json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        )
    except (OSError, json.JSONDecodeError, TypeError):
        return AppSettings()


def save_settings(settings: AppSettings) -> None:
    APP_DIR.mkdir(exist_ok=True)
    SETTINGS_PATH.write_text(
        json.dumps(settings.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def configured_secret() -> str:
    return os.environ.get("NOMAD_AUTH_SECRET", "dev-secret")
