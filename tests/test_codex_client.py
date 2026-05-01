import unittest

from codex_nomad_surface.codex_client import CodexClient, CodexTurnOutput


class CodexClientApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = CodexClient("ws://127.0.0.1:1234")

    def test_mcp_elicitation_is_approval_request(self) -> None:
        message = {
            "id": "approval-1",
            "method": "mcpServer/elicitation/request",
            "params": {"message": "Allow this MCP request?"},
        }

        self.assertTrue(self.client._is_approval_request_message(message))

    def test_mcp_elicitation_response_uses_action_shape(self) -> None:
        approval = {
            "id": "approval-1",
            "method": "mcpServer/elicitation/request",
            "params": {"message": "Allow this MCP request?"},
        }

        self.assertEqual(
            self.client._approval_response_result(approval, "approve"),
            {"action": "accept", "content": {}},
        )
        self.assertEqual(
            self.client._approval_response_result(approval, "reject"),
            {"action": "decline"},
        )

    def test_mcp_elicitation_updates_output_parts(self) -> None:
        message = {
            "id": "approval-1",
            "method": "mcpServer/elicitation/request",
            "params": {"message": "Allow this MCP request?"},
        }
        parts = CodexTurnOutput()
        approvals: list[dict] = []

        changed = self.client._update_output_parts(message, parts, approvals)

        self.assertTrue(changed)
        self.assertEqual(len(approvals), 1)
        snapshot = parts.to_snapshot()
        self.assertEqual(snapshot["approval_request"], "An operation requires approval")

    def test_approval_request_without_top_level_id_still_needs_user_response(
        self,
    ) -> None:
        message = {
            "method": "item/permissions/requestApproval",
            "params": {"approvalId": "approval-1", "permissions": {}},
        }

        self.assertTrue(self.client._is_user_response_request_message(message))
        approval = self.client._approval_from_message(message)
        self.assertEqual(approval["id"], "approval-1")
        self.assertEqual(approval["kind"], "approval_request")

    def test_numeric_zero_request_id_still_needs_user_response(self) -> None:
        message = {
            "id": 0,
            "method": "item/commandExecution/requestApproval",
            "params": {"threadId": "thread-1", "turnId": "turn-1"},
        }

        self.assertTrue(self.client._is_user_response_request_message(message))
        approval = self.client._approval_from_message(message)
        self.assertEqual(approval["id"], 0)

    def test_item_id_only_approval_is_not_user_response_request(self) -> None:
        message = {
            "method": "item/commandExecution/requestApproval",
            "params": {
                "itemId": "item-1",
                "threadId": "thread-1",
                "turnId": "turn-1",
            },
        }

        self.assertFalse(self.client._is_user_response_request_message(message))
        approval = self.client._approval_from_message(message)
        self.assertIsNone(approval["id"])

    def test_unknown_server_request_is_user_response_request(self) -> None:
        message = {
            "id": "request-1",
            "method": "unknown/userInteraction",
            "params": {"question": "Continue?"},
        }

        self.assertTrue(self.client._is_user_response_request_message(message))
        self.assertFalse(self.client._is_approval_request_message(message))
        self.assertEqual(
            self.client._classify_app_server_message(message).kind,
            "response_required",
        )

    def test_resolved_server_request_is_not_user_response_request(self) -> None:
        message = {
            "method": "serverRequest/resolved",
            "params": {
                "threadId": "thread-1",
                "requestId": 2,
            },
        }

        self.assertFalse(self.client._is_user_response_request_message(message))
        self.assertEqual(
            self.client._classify_app_server_message(message).kind,
            "known_silent",
        )

    def test_resolved_server_request_is_handled_without_output(self) -> None:
        parts = CodexTurnOutput()
        approvals: list[dict] = []

        changed = self.client._update_output_parts(
            {
                "method": "serverRequest/resolved",
                "params": {
                    "threadId": "thread-1",
                    "requestId": 2,
                },
            },
            parts,
            approvals=approvals,
        )

        self.assertTrue(changed)
        self.assertEqual(parts.segments, [])
        self.assertEqual(approvals, [])

    def test_unknown_server_request_has_generic_response_shape(self) -> None:
        approval = self.client._approval_from_message(
            {
                "id": "request-1",
                "method": "unknown/userInteraction",
                "params": {"question": "Continue?"},
            }
        )

        self.assertEqual(approval["kind"], "generic_user_response_request")
        self.assertEqual(
            self.client._approval_response_result(approval, "approve"),
            {"accepted": True},
        )
        self.assertEqual(
            self.client._approval_response_result(approval, "reject"),
            {"accepted": False},
        )

    def test_available_decisions_are_exposed_as_response_options(self) -> None:
        approval = self.client._approval_from_message(
            {
                "id": "request-1",
                "method": "item/commandExecution/requestApproval",
                "params": {
                    "availableDecisions": [
                        "accept",
                        "acceptForSession",
                        "decline",
                        "cancel",
                    ]
                },
            }
        )

        self.assertEqual(
            approval["options"],
            [
                {"label": "Accept", "decision": "decision:accept"},
                {
                    "label": "Accept for session",
                    "decision": "decision:acceptForSession",
                },
                {"label": "Decline", "decision": "decision:decline"},
                {"label": "Cancel", "decision": "decision:cancel"},
            ],
        )
        self.assertEqual(
            self.client._approval_response_result(approval, "decision:acceptForSession"),
            {"decision": "acceptForSession"},
        )

    def test_nested_response_id_is_extracted_for_known_approval(self) -> None:
        message = {
            "method": "item/fileChange/requestApproval",
            "params": {
                "request": {"requestId": "nested-request-1"},
                "threadId": "thread-1",
                "turnId": "turn-1",
            },
        }

        self.assertTrue(self.client._is_user_response_request_message(message))
        approval = self.client._approval_from_message(message)
        self.assertEqual(approval["id"], "nested-request-1")

    def test_tool_request_user_input_is_approval_request(self) -> None:
        message = {
            "id": "request-1",
            "method": "item/tool/requestUserInput",
            "params": {
                "questions": {
                    "approval": {
                        "header": "Approval",
                        "question": "Allow this app tool call?",
                        "options": [
                            {"label": "Accept"},
                            {"label": "Decline"},
                            {"label": "Cancel"},
                        ],
                    }
                }
            },
        }

        self.assertTrue(self.client._is_user_response_request_message(message))
        approval = self.client._approval_from_message(message)
        self.assertEqual(approval["kind"], "tool_user_input_request")
        self.assertEqual(
            approval["options"],
            [
                {"label": "Accept", "decision": "option:0"},
                {"label": "Decline", "decision": "option:1"},
                {"label": "Cancel", "decision": "option:2"},
            ],
        )

    def test_tool_request_user_input_response_uses_selected_option(self) -> None:
        approval = self.client._approval_from_message(
            {
                "id": "request-1",
                "method": "tool/requestUserInput",
                "params": {
                    "questions": {
                        "approval": {
                            "question": "Allow this app tool call?",
                            "options": [
                                {"label": "Accept"},
                                {"label": "Decline"},
                            ],
                        }
                    }
                },
            }
        )

        self.assertEqual(
            self.client._approval_response_result(approval, "option:0"),
            {"approval": {"answers": ["Accept"]}},
        )
        self.assertEqual(
            self.client._approval_response_result(approval, "option:1"),
            {"approval": {"answers": ["Decline"]}},
        )

    def test_tool_request_user_input_response_accepts_explicit_answers_json(
        self,
    ) -> None:
        approval = self.client._approval_from_message(
            {
                "id": "request-1",
                "method": "tool/requestUserInput",
                "params": {
                    "questions": {
                        "first": {
                            "question": "First?",
                            "options": [{"label": "A"}, {"label": "B"}],
                        },
                        "second": {
                            "question": "Second?",
                            "options": [{"label": "C"}, {"label": "D"}],
                        },
                    }
                },
            }
        )

        self.assertEqual(approval["options"], [])
        self.assertEqual(
            self.client._approval_response_result(
                approval,
                'answersJson:{"first":{"answers":["A"]},"second":{"answers":["D"]}}',
            ),
            {"first": {"answers": ["A"]}, "second": {"answers": ["D"]}},
        )

    def test_unknown_item_is_preserved_as_other_output(self) -> None:
        parts = CodexTurnOutput()

        changed = self.client._update_output_parts_from_item(
            {"id": "item-1", "type": "futureWidget", "payload": {"value": 1}},
            parts,
        )

        self.assertTrue(changed)
        self.assertEqual(parts.segments[0].kind, "other_event")
        self.assertIn("futureWidget", parts.segments[0].text)

    def test_reasoning_item_without_summary_is_recognized(self) -> None:
        parts = CodexTurnOutput()

        changed = self.client._update_output_parts_from_item(
            {"id": "item-1", "type": "reasoning", "content": []},
            parts,
        )

        self.assertTrue(changed)
        self.assertEqual(parts.segments, [])

    def test_command_execution_item_is_summarized(self) -> None:
        parts = CodexTurnOutput()

        changed = self.client._update_output_parts_from_item(
            {
                "id": "item-1",
                "type": "commandExecution",
                "command": ["python3", "-m", "unittest"],
                "cwd": "/path/to/project",
                "status": "completed",
                "exitCode": 0,
                "durationMs": 123,
                "aggregatedOutput": "OK\nsecond line",
            },
            parts,
        )

        self.assertTrue(changed)
        self.assertEqual(parts.segments[0].kind, "operation_event")
        self.assertIn("Command execution", parts.segments[0].text)
        self.assertIn("python3 -m unittest", parts.segments[0].text)
        self.assertIn("Exit code: `0`", parts.segments[0].text)

    def test_file_change_item_is_summarized(self) -> None:
        parts = CodexTurnOutput()

        changed = self.client._update_output_parts_from_item(
            {
                "id": "item-1",
                "type": "fileChange",
                "status": "completed",
                "changes": [
                    {"path": "app.py", "kind": "update", "diff": "large diff"},
                    {"path": "tests/test_app.py", "kind": "add", "diff": "large diff"},
                ],
            },
            parts,
        )

        self.assertTrue(changed)
        self.assertEqual(parts.segments[0].kind, "operation_event")
        self.assertIn("File change", parts.segments[0].text)
        self.assertIn("Changes: `2`", parts.segments[0].text)
        self.assertIn("`app.py` (update)", parts.segments[0].text)

    def test_mcp_tool_call_item_is_summarized(self) -> None:
        parts = CodexTurnOutput()

        changed = self.client._update_output_parts_from_item(
            {
                "id": "item-1",
                "type": "mcpToolCall",
                "server": "github",
                "tool": "issues/list",
                "status": "inProgress",
                "arguments": {"repo": "example/repo"},
            },
            parts,
        )

        self.assertTrue(changed)
        self.assertEqual(parts.segments[0].kind, "operation_event")
        self.assertIn("MCP tool call", parts.segments[0].text)
        self.assertIn("github", parts.segments[0].text)
        self.assertIn("issues/list", parts.segments[0].text)

    def test_unknown_event_is_preserved_as_other_output(self) -> None:
        parts = CodexTurnOutput()

        changed = self.client._update_output_parts(
            {"method": "future/event", "params": {"value": 1}},
            parts,
            approvals=[],
        )

        self.assertTrue(changed)
        self.assertEqual(parts.segments[0].kind, "other_event")
        self.assertIn("future/event", parts.segments[0].text)

    def test_started_unknown_item_is_preserved_as_other_output(self) -> None:
        parts = CodexTurnOutput()

        changed = self.client._update_output_parts(
            {
                "method": "item/started",
                "params": {"item": {"id": "item-1", "type": "futureWidget"}},
            },
            parts,
            approvals=[],
            stream_items={},
        )

        self.assertTrue(changed)
        self.assertEqual(parts.segments[0].kind, "other_event")
        self.assertIn("futureWidget", parts.segments[0].text)

    def test_known_status_event_is_handled_without_output(self) -> None:
        parts = CodexTurnOutput()

        changed = self.client._update_output_parts(
            {"method": "mcpServer/startupStatus/updated", "params": {"server": "x"}},
            parts,
            approvals=[],
        )

        self.assertTrue(changed)
        self.assertEqual(parts.segments, [])

    def test_skills_changed_event_is_handled_without_output(self) -> None:
        parts = CodexTurnOutput()

        changed = self.client._update_output_parts(
            {"method": "skills/changed", "params": {}},
            parts,
            approvals=[],
        )

        self.assertTrue(changed)
        self.assertEqual(parts.segments, [])


if __name__ == "__main__":
    unittest.main()
