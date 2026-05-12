from __future__ import annotations

from codex_nomad_surface.chat_store import ChatSession
from codex_nomad_surface.settings import Project


def project_key(project: Project) -> str:
    return project.path


def chat_belongs_to_project(chat: ChatSession, project: Project) -> bool:
    return chat.project_path == project.path


def project_select_value(
    projects: list[Project], selected: str, new_project_key: str
) -> str:
    if selected == new_project_key:
        return ""
    for project in projects:
        if selected == project_key(project):
            return project_key(project)
    if not selected and projects:
        return project_key(projects[0])
    return ""


def apply_pending_selectbox_state(
    state: dict, widget_key: str, pending_key: str, fallback: str, options: list[str]
) -> None:
    pending = state.pop(pending_key, None)
    value = str(pending if pending is not None else fallback)
    if pending is not None or str(state.get(widget_key) or "") not in options:
        state[widget_key] = value if value in options else ""
