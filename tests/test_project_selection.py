import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codex_nomad_surface import canvas_store
from codex_nomad_surface.app import (
    project_chats,
    public_query_chat_id,
    recent_thread_chats,
)
from codex_nomad_surface.selection import (
    apply_pending_selectbox_state,
    chat_belongs_to_project,
    project_key,
)
from codex_nomad_surface.chat_store import ChatSession, chat_title_from_text
from codex_nomad_surface.codex_client import CodexThread
from codex_nomad_surface.settings import Project


class ProjectSelectionTests(unittest.TestCase):
    def test_project_key_uses_stable_path(self) -> None:
        project = Project(name="parent/repo", path="/path/to/repo")

        self.assertEqual(project_key(project), "/path/to/repo")

    def test_chat_project_path_is_used_for_project_membership(self) -> None:
        current = Project(name="parent/repo", path="/path/to/repo")
        renamed = Project(name="renamed/repo", path="/path/to/repo")
        other = Project(name="parent/repo", path="/other/repo")
        chat = ChatSession.new("/path/to/repo")

        self.assertTrue(chat_belongs_to_project(chat, current))
        self.assertTrue(chat_belongs_to_project(chat, renamed))
        self.assertFalse(chat_belongs_to_project(chat, other))

    def test_selectbox_keeps_current_valid_widget_value_without_pending(self) -> None:
        state = {"picker": "new"}

        apply_pending_selectbox_state(
            state, "picker", "pending_picker", "old", ["", "old", "new"]
        )

        self.assertEqual(state["picker"], "new")

    def test_selectbox_applies_pending_programmatic_value(self) -> None:
        state = {"picker": "old", "pending_picker": "new"}

        apply_pending_selectbox_state(
            state, "picker", "pending_picker", "old", ["", "old", "new"]
        )

        self.assertEqual(state["picker"], "new")
        self.assertNotIn("pending_picker", state)

    def test_recent_thread_chats_sort_across_projects(self) -> None:
        projects = [
            Project(name="alpha", path="/path/to/alpha"),
            Project(name="beta", path="/path/to/beta"),
        ]
        threads = [
            CodexThread(
                id="old",
                preview="Old thread",
                cwd="/path/to/alpha",
                created_at=100,
                updated_at=100,
            ),
            CodexThread(
                id="new",
                preview="New thread",
                cwd="/path/to/beta",
                created_at=50,
                updated_at=300,
            ),
            CodexThread(
                id="missing-project",
                preview="Hidden thread",
                cwd="/path/to/other",
                created_at=400,
                updated_at=400,
            ),
        ]

        recent = recent_thread_chats(threads, projects)

        self.assertEqual(
            [(project.path, chat.id) for project, chat in recent],
            [
                ("/path/to/beta", "thread:new"),
                ("/path/to/alpha", "thread:old"),
            ],
        )

    def test_recent_thread_chats_respects_limit(self) -> None:
        projects = [Project(name="repo", path="/path/to/repo")]
        threads = [
            CodexThread(
                id=str(index),
                preview=f"Thread {index}",
                cwd="/path/to/repo",
                created_at=index,
                updated_at=index,
            )
            for index in range(4)
        ]

        recent = recent_thread_chats(threads, projects, limit=2)

        self.assertEqual([chat.thread_id for _, chat in recent], ["3", "2"])

    def test_recent_thread_chats_restores_canvas_draft(self) -> None:
        projects = [Project(name="repo", path="/path/to/repo")]
        with TemporaryDirectory() as temporary_directory:
            previous_root = canvas_store.CANVAS_ROOT
            canvas_store.CANVAS_ROOT = Path(temporary_directory) / "canvases"
            try:
                manifest = canvas_store.initialize_canvas_draft(
                    "draft-chat", "/path/to/repo"
                )

                recent = recent_thread_chats([], projects)
            finally:
                canvas_store.CANVAS_ROOT = previous_root

        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0][1].id, f"canvas:{manifest['canvas_id']}")
        self.assertIsNone(recent[0][1].thread_id)
        self.assertEqual(recent[0][1].canvas_id, manifest["canvas_id"])

    def test_canvas_draft_id_is_preserved_in_query_string(self) -> None:
        self.assertEqual(
            public_query_chat_id("canvas:canvas-abcd"),
            "canvas:canvas-abcd",
        )

    def test_project_chats_restores_canvas_draft(self) -> None:
        project = Project(name="repo", path="/path/to/repo")
        with TemporaryDirectory() as temporary_directory:
            previous_root = canvas_store.CANVAS_ROOT
            canvas_store.CANVAS_ROOT = Path(temporary_directory) / "canvases"
            try:
                manifest = canvas_store.initialize_canvas_draft(
                    "draft-chat", project.path
                )
                with patch("codex_nomad_surface.app.chats_state", return_value=[]):
                    chats = project_chats(project, [])
            finally:
                canvas_store.CANVAS_ROOT = previous_root

        self.assertEqual(
            [chat.id for chat in chats],
            [f"canvas:{manifest['canvas_id']}"],
        )

    def test_chat_title_marks_truncated_text(self) -> None:
        text = "x" * 60

        title = chat_title_from_text(text)

        self.assertEqual(len(title), 48)
        self.assertTrue(title.endswith("..."))


if __name__ == "__main__":
    unittest.main()


def test_project_creation_selects_new_chat_without_starting_thread(monkeypatch):
    from contextlib import nullcontext
    from unittest.mock import Mock
    from codex_nomad_surface import app

    class State(dict):
        __getattr__ = dict.__getitem__
        __setattr__ = dict.__setitem__

    state = State(
        selected_project_key="", selected_chat_id="thread:old",
        manual_project_paths=[], new_project_path="", draft_chat=None,
    )
    query = {"chat": "thread:old"}
    monkeypatch.setattr(app.st, "session_state", state)
    monkeypatch.setattr(app.st, "query_params", query)
    monkeypatch.setattr(app.st, "form", lambda *a, **k: nullcontext())
    monkeypatch.setattr(app.st, "text_input", lambda *a, **k: "/path/to/project")
    monkeypatch.setattr(app.st, "form_submit_button", lambda *a, **k: True)
    for name in ("subheader", "success", "rerun"):
        monkeypatch.setattr(app.st, name, lambda *a, **k: None)
    monkeypatch.setattr(app, "render_recent_threads", lambda *a: None)
    client = Mock()

    app.project_creation_workspace(client, [])

    client.start_thread.assert_not_called()
    assert state.manual_project_paths == ["/path/to/project"]
    assert state.selected_project_key == "/path/to/project"
    assert state[app.PENDING_PROJECT_SELECT_KEY] == "/path/to/project"
    assert state.selected_chat_id == ""
    assert state[app.PENDING_CHAT_SELECT_KEY] == ""
    assert "chat" not in query
    project = Project(name="project", path="/path/to/project")
    draft = app.draft_chat(project)
    assert draft.thread_id is None
    assert not draft.messages
    assert draft.surface == "chat"
