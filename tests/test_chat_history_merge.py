import unittest

from codex_nomad_surface.app import (
    codex_output_is_progress_only,
    merge_thread_history_messages,
)
from codex_nomad_surface.chat_store import ChatMessage


class ChatHistoryMergeTests(unittest.TestCase):
    def test_merge_prefers_server_item_identity(self) -> None:
        existing = [
            ChatMessage(
                role="user",
                content="Hello",
                metadata={"server_turn_id": "turn-1", "server_item_id": "item-1"},
            )
        ]
        loaded = [
            ChatMessage(
                role="user",
                content="Hello",
                metadata={"server_turn_id": "turn-1", "server_item_id": "item-1"},
            )
        ]

        self.assertEqual(len(merge_thread_history_messages(existing, loaded)), 1)

    def test_merge_matches_turn_role_when_existing_message_lacks_item_id(self) -> None:
        existing = [
            ChatMessage(
                role="user",
                content="Hello",
                metadata={"server_turn_id": "turn-1", "run_id": "run-1"},
            )
        ]
        loaded = [
            ChatMessage(
                role="user",
                content="Hello",
                metadata={"server_turn_id": "turn-1", "server_item_id": "item-1"},
            )
        ]

        self.assertEqual(len(merge_thread_history_messages(existing, loaded)), 1)

    def test_merge_does_not_collapse_same_text_without_server_ids(self) -> None:
        existing = [ChatMessage(role="user", content="Again", metadata={"run_id": "1"})]
        loaded = [ChatMessage(role="user", content="Again", metadata={"run_id": "2"})]

        self.assertEqual(len(merge_thread_history_messages(existing, loaded)), 2)

    def test_codex_output_is_progress_only_when_final_answer_is_missing(self) -> None:
        self.assertTrue(
            codex_output_is_progress_only(
                {
                    "output": "",
                    "segments": [
                        {
                            "kind": "commentary",
                            "text": "Working",
                            "item_id": "item-1",
                        }
                    ],
                }
            )
        )

    def test_codex_output_is_not_progress_only_with_final_answer(self) -> None:
        self.assertFalse(
            codex_output_is_progress_only(
                {
                    "output": "Done",
                    "segments": [
                        {
                            "kind": "commentary",
                            "text": "Working",
                            "item_id": "item-1",
                        }
                    ],
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
