import unittest

from codex_client import CodexClient, CodexTurnOutput


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

    def test_unknown_server_request_is_user_response_request(self) -> None:
        message = {
            "id": "request-1",
            "method": "unknown/userInteraction",
            "params": {"question": "Continue?"},
        }

        self.assertTrue(self.client._is_user_response_request_message(message))
        self.assertFalse(self.client._is_approval_request_message(message))

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

    def test_unknown_item_is_preserved_as_other_output(self) -> None:
        parts = CodexTurnOutput()

        changed = self.client._update_output_parts_from_item(
            {"id": "item-1", "type": "futureWidget", "payload": {"value": 1}},
            parts,
        )

        self.assertTrue(changed)
        self.assertEqual(parts.segments[0].kind, "other_event")
        self.assertIn("futureWidget", parts.segments[0].text)

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


if __name__ == "__main__":
    unittest.main()
