import unittest

from codex_nomad_surface.app import (
    canvas_confirmed_message_items,
    codex_output_is_progress_only,
    latest_progress_only_message_index,
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

    def test_later_user_message_does_not_hide_latest_progress_recovery(self) -> None:
        messages = [
            (
                2,
                ChatMessage(
                    role="assistant",
                    content="",
                    metadata={
                        "codex_output": {
                            "segments": [
                                {"kind": "commentary", "text": "Working"}
                            ]
                        }
                    },
                ),
            ),
            (
                3,
                ChatMessage(
                    role="user",
                    content="Additional direction",
                    metadata={"kind": "turn_steer"},
                ),
            ),
        ]

        self.assertEqual(latest_progress_only_message_index(messages), 2)

    def test_later_final_answer_hides_obsolete_progress_recovery(self) -> None:
        messages = [
            (
                2,
                ChatMessage(
                    role="assistant",
                    content="",
                    metadata={
                        "codex_output": {
                            "segments": [
                                {"kind": "commentary", "text": "Working"}
                            ]
                        }
                    },
                ),
            ),
            (3, ChatMessage(role="assistant", content="Finished")),
        ]

        self.assertIsNone(latest_progress_only_message_index(messages))

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

    def test_canvas_history_is_bounded_without_mutating_chat(self) -> None:
        chat = ChatSession.new("/project")
        chat.add_message("user", "First")
        chat.add_message("assistant", "Previous response")
        chat.add_message(
            "user",
            "Current request",
            metadata={"kind": "turn_prompt", "run_id": "run-1"},
        )

        items = canvas_confirmed_message_items(
            chat,
            {"chat_id": chat.id, "run_id": "run-1"},
            limit=1,
        )

        self.assertEqual([message.content for _, message in items], ["Previous response"])
        self.assertEqual(len(chat.messages), 3)

    def test_canvas_history_can_hide_all_completed_messages(self) -> None:
        chat = ChatSession.new("/project")
        chat.add_message("assistant", "Previous response")

        self.assertEqual(canvas_confirmed_message_items(chat, None, limit=0), [])


if __name__ == "__main__":
    unittest.main()
