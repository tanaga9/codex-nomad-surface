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
    source: str
    source_label: str
    form: dict[str, Any]


def load_promptform_defs(project_path: str = "") -> list[PromptFormDef]:
    defs: list[PromptFormDef] = []
    roots: list[tuple[str, Path, str]] = []
    seen_roots: set[Path] = set()

    if project_path:
        project_defs_dir = Path(project_path).expanduser() / PROMPTFORM_DEFS_DIR
        resolved = _resolved_path(project_defs_dir)
        if resolved not in seen_roots:
            roots.append(("project", project_defs_dir, "Project"))
            seen_roots.add(resolved)

    server_defs_dir = PROMPTFORM_DEFS_DIR
    resolved = _resolved_path(server_defs_dir)
    if resolved not in seen_roots:
        roots.append(("server", server_defs_dir, "General"))

    for source, root, source_label in roots:
        defs.extend(_load_promptform_defs_from_root(source, root, source_label))
    return defs


def _load_promptform_defs_from_root(
    source: str, root: Path, source_label: str
) -> list[PromptFormDef]:
    defs: list[PromptFormDef] = []
    for path in sorted(root.rglob("*.json")):
        try:
            relative_path = path.relative_to(root)
            raw = json.loads(path.read_text(encoding="utf-8"))
            defs.append(
                PromptFormDef(
                    id=f"{source}:{relative_path.with_suffix('')}",
                    path=str(PROMPTFORM_DEFS_DIR / relative_path),
                    source=source,
                    source_label=source_label,
                    form=dict(raw),
                )
            )
        except (OSError, TypeError, json.JSONDecodeError, ValueError):
            continue
    return defs


def _resolved_path(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def promptform_def_by_id(
    defs: list[PromptFormDef], promptform_def_id: str
) -> PromptFormDef | None:
    for item in defs:
        if item.id == promptform_def_id:
            return item
    for item in defs:
        if item.id.split(":", 1)[-1] == promptform_def_id:
            return item
    return None
