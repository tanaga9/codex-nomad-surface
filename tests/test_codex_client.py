import asyncio
import json
import unittest

from codex_nomad_surface.codex_client import (
    CODEX_CLIENT_INFO,
    CodexClient,
    CodexTurnOutput,
    _codex_initialize_params,
)


class CodexClientApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = CodexClient("ws://127.0.0.1:1234")

    def test_initialize_client_info_uses_hardcoded_project_metadata(self) -> None:
        params = _codex_initialize_params()

        self.assertEqual(params["clientInfo"]["name"], "codex-nomad-surface")
        self.assertEqual(params["clientInfo"]["version"], "0.1.0")
        self.assertEqual(params["clientInfo"]["title"], "Codex Nomad Surface")
        self.assertEqual(params["clientInfo"], CODEX_CLIENT_INFO)

    def test_initialize_params_return_copies(self) -> None:
        params = _codex_initialize_params()
        params["clientInfo"]["name"] = "changed"

        self.assertEqual(CODEX_CLIENT_INFO["name"], "codex-nomad-surface")

    def test_model_list_result_reports_non_websocket_url(self) -> None:
        client = CodexClient("http://127.0.0.1:8080")

        result = client.list_models_result()

        self.assertEqual(result.models, [])
        self.assertEqual(result.error, "Could not generate the model list.")
        self.assertEqual(client.list_models(), [])

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

    def test_duplicate_approval_response_is_ignored_before_event_loop_reentry(
        self,
    ) -> None:
        loop = type(
            "FakeLoop",
            (),
            {"is_running": lambda self: False, "is_closed": lambda self: False},
        )()
        runtime = {"loop": loop, "approval_response_in_progress": True}

        result = self.client.respond_chat_turn(
            runtime,
            {"id": "approval-1"},
            "approve",
        )

        self.assertEqual(result["status"], "duplicate_approval_response")

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

    def test_permissions_approval_can_be_scoped_to_thread(self) -> None:
        approval = self.client._approval_from_message(
            {
                "method": "item/permissions/requestApproval",
                "params": {
                    "approvalId": "approval-1",
                    "permissions": {"network": {"domains": {"example.com": "allow"}}},
                },
            }
        )

        self.assertEqual(
            self.client._approval_response_result(approval, "approve"),
            {
                "permissions": {"network": {"domains": {"example.com": "allow"}}},
                "scope": "turn",
            },
        )
        self.assertEqual(
            self.client._approval_response_result(approval, "approveForThread"),
            {
                "permissions": {"network": {"domains": {"example.com": "allow"}}},
                "scope": "thread",
            },
        )

    def test_command_approval_detail_is_human_readable(self) -> None:
        approval = self.client._approval_from_message(
            {
                "method": "item/commandExecution/requestApproval",
                "params": {
                    "approvalId": "approval-1",
                    "reason": "Needs network access for dependency download.",
                    "command": ["npm", "install"],
                    "cwd": "/path/to/project",
                    "availableDecisions": ["accept", "decline"],
                },
            }
        )

        self.assertIn(
            "Reason: Needs network access for dependency download.",
            approval["detail"],
        )
        self.assertIn("Command: npm install", approval["detail"])
        self.assertIn("Directory: /path/to/project", approval["detail"])
        self.assertIn("Choices: Accept, Decline", approval["detail"])
        self.assertNotIn("{", approval["detail"])

    def test_permission_approval_detail_summarizes_permissions(self) -> None:
        approval = self.client._approval_from_message(
            {
                "method": "item/permissions/requestApproval",
                "params": {
                    "approvalId": "approval-1",
                    "reason": "Allow browser connector.",
                    "permissions": {"apps": {"browser": "allow"}},
                },
            }
        )

        self.assertIn("Reason: Allow browser connector.", approval["detail"])
        self.assertIn("Permissions: apps=browser=allow", approval["detail"])
        self.assertNotIn("{", approval["detail"])

    def test_file_change_approval_detail_includes_grant_root(self) -> None:
        approval = self.client._approval_from_message(
            {
                "method": "item/fileChange/requestApproval",
                "params": {
                    "approvalId": "approval-1",
                    "reason": "Allow writing generated files.",
                    "grantRoot": "/path/to/project/generated",
                },
            }
        )

        self.assertIn("Reason: Allow writing generated files.", approval["detail"])
        self.assertIn("Grant root: /path/to/project/generated", approval["detail"])
        self.assertNotIn("{", approval["detail"])

    def test_permissions_approval_exposes_dynamic_scope_options(self) -> None:
        approval = self.client._approval_from_message(
            {
                "method": "item/permissions/requestApproval",
                "params": {
                    "approvalId": "approval-1",
                    "permissions": {"network": {"domains": {"example.com": "allow"}}},
                    "availableScopes": [
                        "turn",
                        {"value": "thread", "label": "Allow in this thread"},
                        {"value": "project", "label": "Allow for this project"},
                    ],
                },
            }
        )

        self.assertEqual(
            approval["options"],
            [
                {"label": "Allow once", "decision": "permissionScope:turn"},
                {
                    "label": "Allow in this thread",
                    "decision": "permissionScope:thread",
                },
                {
                    "label": "Allow for this project",
                    "decision": "permissionScope:project",
                },
                {"label": "Decline", "decision": "reject"},
            ],
        )
        self.assertEqual(
            self.client._approval_response_result(approval, "permissionScope:project"),
            {
                "permissions": {"network": {"domains": {"example.com": "allow"}}},
                "scope": "project",
            },
        )

    def test_permissions_approval_uses_explicit_response_options(self) -> None:
        approval = self.client._approval_from_message(
            {
                "method": "item/permissions/requestApproval",
                "params": {
                    "approvalId": "approval-1",
                    "options": [
                        {
                            "label": "Allow while this app is open",
                            "response": {
                                "permissions": {"apps": {"browser": "allow"}},
                                "scope": "appSession",
                            },
                        }
                    ],
                },
            }
        )

        self.assertEqual(
            approval["options"],
            [
                {
                    "label": "Allow while this app is open",
                    "decision": (
                        'responseJson:{"permissions":{"apps":{"browser":"allow"}},'
                        '"scope":"appSession"}'
                    ),
                },
                {"label": "Decline", "decision": "reject"},
            ],
        )
        self.assertEqual(
            self.client._approval_response_result(
                approval,
                'responseJson:{"permissions":{"apps":{"browser":"allow"}},"scope":"appSession"}',
            ),
            {"permissions": {"apps": {"browser": "allow"}}, "scope": "appSession"},
        )

    def test_permissions_approval_does_not_duplicate_explicit_negative_option(
        self,
    ) -> None:
        approval = self.client._approval_from_message(
            {
                "method": "item/permissions/requestApproval",
                "params": {
                    "approvalId": "approval-1",
                    "options": [
                        {
                            "label": "Allow while this app is open",
                            "response": {
                                "permissions": {"apps": {"browser": "allow"}},
                                "scope": "appSession",
                            },
                        },
                        {
                            "label": "Cancel",
                            "response": {"permissions": {}, "scope": "turn"},
                        },
                    ],
                },
            }
        )

        self.assertEqual(len(approval["options"]), 2)
        self.assertEqual(approval["options"][1]["label"], "Cancel")

    def test_permissions_approval_does_not_infer_scope_from_generic_options(
        self,
    ) -> None:
        approval = self.client._approval_from_message(
            {
                "method": "item/permissions/requestApproval",
                "params": {
                    "approvalId": "approval-1",
                    "permissions": {"network": {"domains": {"example.com": "allow"}}},
                    "options": [{"label": "Maybe later", "value": "thread"}],
                },
            }
        )

        self.assertEqual(approval["options"], [])

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
        self.assertIn("exit 0", parts.segments[0].text)
        self.assertNotIn("\n", parts.segments[0].text)

    def test_command_execution_summary_wraps_arbitrary_output_as_code(self) -> None:
        parts = CodexTurnOutput()

        changed = self.client._update_output_parts_from_item(
            {
                "id": "item-1",
                "type": "commandExecution",
                "command": ["printf", "`**value**`"],
                "aggregatedOutput": "**not bold**\n# not a heading",
            },
            parts,
        )

        self.assertTrue(changed)
        text = parts.segments[0].text
        self.assertIn("``printf '`**value**`'``", text)
        self.assertIn("output `**not bold**`", text)
        self.assertNotIn("# not a heading", text)

    def test_command_execution_summary_truncates_long_output(self) -> None:
        parts = CodexTurnOutput()

        changed = self.client._update_output_parts_from_item(
            {
                "id": "item-1",
                "type": "commandExecution",
                "aggregatedOutput": "x" * 260,
            },
            parts,
        )

        self.assertTrue(changed)
        text = parts.segments[0].text
        self.assertIn("...", text)
        self.assertLess(len(text), 290)

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
        self.assertIn("2 changes", parts.segments[0].text)
        self.assertIn("`app.py` (update)", parts.segments[0].text)
        self.assertNotIn("\n", parts.segments[0].text)

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
        self.assertNotIn("\n", parts.segments[0].text)

    def test_thread_history_messages_preserve_server_ids(self) -> None:
        result = self.client._thread_messages_from_turns_list(
            {
                "data": [
                    {
                        "id": "turn-1",
                        "items": [
                            {
                                "id": "user-item-1",
                                "type": "userMessage",
                                "content": [{"text": "Hello"}],
                            },
                            {
                                "id": "agent-item-1",
                                "type": "agentMessage",
                                "text": "Hi",
                                "phase": "final_answer",
                            },
                        ],
                    }
                ]
            }
        )

        self.assertEqual(len(result.messages), 2)
        self.assertEqual(
            result.messages[0]["metadata"],
            {"server_turn_id": "turn-1", "server_item_id": "user-item-1"},
        )
        self.assertEqual(result.messages[1]["metadata"]["server_turn_id"], "turn-1")
        self.assertEqual(
            result.messages[1]["metadata"]["server_item_ids"], ["agent-item-1"]
        )

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

    def test_send_turn_steer_uses_active_turn_rpc_shape(self) -> None:
        class FakeWebSocket:
            def __init__(self) -> None:
                self.sent: list[str] = []

            async def send(self, payload: str) -> None:
                self.sent.append(payload)

        websocket = FakeWebSocket()
        runtime = {
            "websocket": websocket,
            "thread_id": "thread-1",
            "turn_id": "turn-1",
            "control_request_ids": set(),
        }

        result = asyncio.run(self.client._send_turn_steer_ws(runtime, "Adjust course."))

        self.assertTrue(result["ok"])
        payload = json.loads(websocket.sent[0])
        self.assertEqual(payload["method"], "turn/steer")
        self.assertEqual(payload["params"]["threadId"], "thread-1")
        self.assertEqual(payload["params"]["expectedTurnId"], "turn-1")
        self.assertEqual(payload["params"]["input"][0]["text"], "Adjust course.")
        self.assertIn(payload["id"], runtime["control_request_ids"])
        self.assertEqual(
            runtime["control_request_actions"][payload["id"]], "turn/steer"
        )

    def test_interrupt_turn_uses_active_turn_rpc_shape(self) -> None:
        class FakeWebSocket:
            def __init__(self) -> None:
                self.sent: list[str] = []

            async def send(self, payload: str) -> None:
                self.sent.append(payload)

        websocket = FakeWebSocket()
        runtime = {
            "websocket": websocket,
            "thread_id": "thread-1",
            "turn_id": "turn-1",
            "control_request_ids": set(),
        }

        result = asyncio.run(self.client._send_turn_interrupt_ws(runtime))

        self.assertTrue(result["ok"])
        payload = json.loads(websocket.sent[0])
        self.assertEqual(payload["method"], "turn/interrupt")
        self.assertEqual(payload["params"]["threadId"], "thread-1")
        self.assertEqual(payload["params"]["turnId"], "turn-1")
        self.assertIn(payload["id"], runtime["control_request_ids"])
        self.assertEqual(
            runtime["control_request_actions"][payload["id"]], "turn/interrupt"
        )

    def test_interrupt_turn_rpc_error_marks_runtime(self) -> None:
        class FakeWebSocket:
            def __init__(self) -> None:
                self.messages = [
                    json.dumps(
                        {
                            "id": "request-1",
                            "error": {"message": "turn is no longer active"},
                        }
                    ),
                    json.dumps(
                        {
                            "method": "turn/completed",
                            "params": {"threadId": "thread-1"},
                        }
                    ),
                ]
                self.closed = False

            async def recv(self) -> str:
                return self.messages.pop(0)

            async def close(self) -> None:
                self.closed = True

        websocket = FakeWebSocket()
        runtime = {
            "websocket": websocket,
            "thread_id": "thread-1",
            "turn_id": "turn-1",
            "output_parts": CodexTurnOutput(),
            "stream_items": {},
            "approvals": [],
            "control_request_ids": {"request-1"},
            "control_request_actions": {"request-1": "turn/interrupt"},
        }

        result = asyncio.run(self.client._collect_chat_turn_ws(runtime))

        self.assertTrue(result["ok"])
        self.assertEqual(runtime["interrupt_error"], "turn is no longer active")
        self.assertEqual(result["output_parts"]["errors"], "turn is no longer active")
        self.assertTrue(websocket.closed)


if __name__ == "__main__":
    unittest.main()
