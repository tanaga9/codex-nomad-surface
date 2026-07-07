import unittest

from codex_nomad_surface.app import recent_thread_chats
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

    def test_chat_title_marks_truncated_text(self) -> None:
        text = "x" * 60

        title = chat_title_from_text(text)

        self.assertEqual(len(title), 48)
        self.assertTrue(title.endswith("..."))


if __name__ == "__main__":
    unittest.main()
