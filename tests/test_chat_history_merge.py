import unittest

from codex_nomad_surface.app import (
    codex_output_is_progress_only,
    codex_output_other_segments,
    merge_thread_history_messages,
    set_user_turn_delivery_status,
    user_message_needs_copy_backup,
)
from codex_nomad_surface.chat_store import ChatMessage, ChatSession


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

    def test_codex_output_other_segments_keeps_unknown_events_visible(self) -> None:
        segments = [
            {"kind": "final_answer", "text": "Done"},
            {"kind": "commentary", "text": "Working"},
            {"kind": "operation_event", "text": "Ran command"},
            {"kind": "other_event", "text": "Unrecognized event: `future/event`"},
            {"kind": "future_widget", "text": "New output shape"},
        ]

        self.assertEqual(
            codex_output_other_segments(segments),
            [
                {"kind": "other_event", "text": "Unrecognized event: `future/event`"},
                {"kind": "future_widget", "text": "New output shape"},
            ],
        )

    def test_unconfirmed_user_turn_keeps_copy_backup_available(self) -> None:
        self.assertTrue(user_message_needs_copy_backup({"delivery_status": "sending"}))
        self.assertTrue(user_message_needs_copy_backup({"delivery_status": "failed"}))
        self.assertFalse(user_message_needs_copy_backup({"delivery_status": "delivered"}))

    def test_delivery_status_is_updated_for_the_matching_turn(self) -> None:
        chat = ChatSession.new("/project")
        chat.add_message(
            "user",
            "Keep this prompt",
            metadata={
                "kind": "turn_prompt",
                "run_id": "run-1",
                "delivery_status": "sending",
            },
        )

        set_user_turn_delivery_status(chat, "run-1", "delivered")

        self.assertEqual(chat.messages[0].metadata["delivery_status"], "delivered")


if __name__ == "__main__":
    unittest.main()
