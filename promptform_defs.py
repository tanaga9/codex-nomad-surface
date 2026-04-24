from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROMPTFORM_DEFS_DIR = Path("promptform-defs")


@dataclass(frozen=True)
class PromptFormDef:
    id: str
    path: str
    form: dict[str, Any]


def load_promptform_defs() -> list[PromptFormDef]:
    defs: list[PromptFormDef] = []
    for path in sorted(PROMPTFORM_DEFS_DIR.rglob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            defs.append(
                PromptFormDef(
                    id=str(path.relative_to(PROMPTFORM_DEFS_DIR).with_suffix("")),
                    path=str(path.relative_to(PROMPTFORM_DEFS_DIR)),
                    form=dict(raw),
                )
            )
        except (OSError, TypeError, json.JSONDecodeError, ValueError):
            continue
    return defs


def promptform_def_by_id(
    defs: list[PromptFormDef], promptform_def_id: str
) -> PromptFormDef | None:
    for item in defs:
        if item.id == promptform_def_id:
            return item
    return None
