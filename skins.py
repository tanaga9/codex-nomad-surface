from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SKINS_DIR = Path("skins")


@dataclass(frozen=True)
class Skin:
    id: str
    name: str
    description: str
    placeholder: str
    quick_prompts: list[str]
    fields: list[dict[str, Any]]


def load_skins() -> list[Skin]:
    skins: list[Skin] = []
    for path in sorted(SKINS_DIR.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            skins.append(
                Skin(
                    id=raw["id"],
                    name=raw["name"],
                    description=raw.get("description", ""),
                    placeholder=raw.get("placeholder", "Request for Codex"),
                    quick_prompts=list(raw.get("quick_prompts", [])),
                    fields=list(raw.get("fields", [])),
                )
            )
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            continue
    return skins


def skin_by_id(skins: list[Skin], skin_id: str) -> Skin:
    for skin in skins:
        if skin.id == skin_id:
            return skin
    return skins[0]
