import unittest

from codex_nomad_surface.selection import (
    apply_pending_selectbox_state,
    chat_belongs_to_project,
    project_key,
)
from codex_nomad_surface.chat_store import ChatSession
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


if __name__ == "__main__":
    unittest.main()
