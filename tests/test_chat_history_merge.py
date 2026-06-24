import unittest

from codex_nomad_surface.app import (
    merge_recent_thread_history_messages,
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

    def test_recent_merge_preserves_old_prefix_and_replaces_latest_overlap(self) -> None:
        existing = [
            ChatMessage(
                role="user",
                content="Old",
                metadata={"server_turn_id": "turn-1", "server_item_id": "item-1"},
            ),
            ChatMessage(
                role="user",
                content="Prompt",
                metadata={"server_turn_id": "turn-2", "server_item_id": "item-2"},
            ),
            ChatMessage(
                role="assistant",
                content="Working",
                metadata={
                    "server_turn_id": "turn-2",
                    "server_item_ids": ["agent-item-1"],
                    "codex_output": {
                        "segments": [
                            {
                                "kind": "commentary",
                                "text": "Working",
                                "item_id": "agent-item-1",
                            }
                        ]
                    },
                },
            ),
        ]
        loaded = [
            ChatMessage(
                role="user",
                content="Prompt",
                metadata={"server_turn_id": "turn-2", "server_item_id": "item-2"},
            ),
            ChatMessage(
                role="assistant",
                content="Done",
                metadata={
                    "server_turn_id": "turn-2",
                    "server_item_ids": ["agent-item-1", "agent-item-2"],
                    "codex_output": {"output": "Done"},
                },
            ),
        ]

        merged = merge_recent_thread_history_messages(existing, loaded)

        self.assertEqual(
            [message.content for message in merged], ["Old", "Prompt", "Done"]
        )

    def test_recent_merge_appends_when_recent_history_has_no_overlap(self) -> None:
        existing = [
            ChatMessage(
                role="user",
                content="Old",
                metadata={"server_turn_id": "turn-1", "server_item_id": "item-1"},
            )
        ]
        loaded = [
            ChatMessage(
                role="user",
                content="New",
                metadata={"server_turn_id": "turn-2", "server_item_id": "item-2"},
            )
        ]

        merged = merge_recent_thread_history_messages(existing, loaded)

        self.assertEqual([message.content for message in merged], ["Old", "New"])

    def test_recent_merge_accumulates_assistant_progress_segments(self) -> None:
        existing = [
            ChatMessage(
                role="assistant",
                content="",
                metadata={
                    "server_turn_id": "turn-1",
                    "server_item_ids": ["item-1"],
                    "codex_output": {
                        "segments": [
                            {
                                "kind": "commentary",
                                "text": "Working",
                                "item_id": "item-1",
                            }
                        ]
                    },
                },
            )
        ]
        loaded = [
            ChatMessage(
                role="assistant",
                content="",
                metadata={
                    "server_turn_id": "turn-1",
                    "server_item_ids": ["item-2"],
                    "codex_output": {
                        "segments": [
                            {
                                "kind": "commentary",
                                "text": "Still working",
                                "item_id": "item-2",
                            }
                        ]
                    },
                },
            )
        ]

        merged = merge_recent_thread_history_messages(existing, loaded)
        segments = merged[0].metadata["codex_output"]["segments"]

        self.assertEqual(
            [segment["text"] for segment in segments], ["Working", "Still working"]
        )
        self.assertEqual(merged[0].metadata["server_item_ids"], ["item-1", "item-2"])

    def test_recent_merge_updates_same_progress_item(self) -> None:
        existing = [
            ChatMessage(
                role="assistant",
                content="",
                metadata={
                    "server_turn_id": "turn-1",
                    "server_item_ids": ["item-1"],
                    "codex_output": {
                        "segments": [
                            {
                                "kind": "operation_event",
                                "text": "Command execution - inProgress",
                                "item_id": "item-1",
                            }
                        ]
                    },
                },
            )
        ]
        loaded = [
            ChatMessage(
                role="assistant",
                content="",
                metadata={
                    "server_turn_id": "turn-1",
                    "server_item_ids": ["item-1"],
                    "codex_output": {
                        "segments": [
                            {
                                "kind": "operation_event",
                                "text": "Command execution - completed",
                                "item_id": "item-1",
                            }
                        ]
                    },
                },
            )
        ]

        merged = merge_recent_thread_history_messages(existing, loaded)
        segments = merged[0].metadata["codex_output"]["segments"]

        self.assertEqual(
            [segment["text"] for segment in segments],
            ["Command execution - completed"],
        )


if __name__ == "__main__":
    unittest.main()
