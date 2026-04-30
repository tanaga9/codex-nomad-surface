from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SkillDef:
    id: str
    name: str
    description: str
    path: str


def skill_defs_from_app_server(skills: list[dict]) -> list[SkillDef]:
    defs: list[SkillDef] = []
    for index, skill in enumerate(skills):
        if skill.get("enabled") is False:
            continue
        name = str(skill.get("name") or "").strip()
        if not name:
            continue
        interface = (
            skill.get("interface") if isinstance(skill.get("interface"), dict) else {}
        )
        description = str(
            interface.get("shortDescription") or skill.get("description") or ""
        ).strip()
        path = str(skill.get("path") or skill.get("manifestPath") or "").strip()
        defs.append(
            SkillDef(
                id=path or f"{name}-{index}",
                name=name,
                description=description,
                path=path,
            )
        )
    return with_unique_ids(defs)


def skill_def_by_id(defs: list[SkillDef], skill_id: str) -> SkillDef | None:
    for item in defs:
        if item.id == skill_id:
            return item
    return None


def with_unique_ids(defs: list[SkillDef]) -> list[SkillDef]:
    sorted_defs = sorted(defs, key=lambda item: (item.name.lower(), item.path))
    counts: dict[str, int] = {}
    unique_defs: list[SkillDef] = []
    for item in sorted_defs:
        base_id = item.name
        counts[base_id] = counts.get(base_id, 0) + 1
        item_id = base_id if counts[base_id] == 1 else f"{base_id} ({counts[base_id]})"
        unique_defs.append(
            SkillDef(
                id=item_id,
                name=item.name,
                description=item.description,
                path=item.path,
            )
        )
    return unique_defs
