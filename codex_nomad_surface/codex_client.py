from __future__ import annotations

import asyncio
import json
import shlex
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ConnectionStatus:
    ok: bool
    label: str
    detail: str = ""


@dataclass
class CodexThread:
    id: str
    preview: str
    cwd: str
    created_at: int = 0
    updated_at: int = 0
    status: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CodexThread":
        status_raw = raw.get("status") or {}
        status = (
            status_raw.get("type")
            if isinstance(status_raw, dict)
            else str(status_raw or "")
        )
        return cls(
            id=str(raw.get("id") or ""),
            preview=str(raw.get("preview") or raw.get("name") or "New Chat"),
            cwd=str(raw.get("cwd") or ""),
            created_at=int(raw.get("createdAt") or 0),
            updated_at=int(raw.get("updatedAt") or raw.get("createdAt") or 0),
            status=status or "",
        )


@dataclass
class CodexThreadMessages:
    messages: list[dict[str, Any]]
    cursor: str | None = None
    has_older: bool = False


class ApprovalRequired(Exception):
    pass


@dataclass
class CodexOutputSegment:
    kind: str
    text: str
    item_id: str = ""
    phase: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "text": self.text,
            "item_id": self.item_id,
            "phase": self.phase,
            "metadata": self.metadata.copy(),
        }


@dataclass
class CodexTurnOutput:
    segments: list[CodexOutputSegment] = field(default_factory=list)

    def append_delta(
        self,
        kind: str,
        delta: str,
        item_id: str = "",
        phase: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not delta:
            return
        segment = self._find_or_reclassify_segment(kind, item_id)
        if segment:
            segment.text += delta
            if phase:
                segment.phase = phase
            if metadata:
                segment.metadata.update(metadata)
            return
        self.segments.append(
            CodexOutputSegment(kind, delta, item_id, phase, metadata or {})
        )

    def append_block(
        self,
        kind: str,
        text: str,
        item_id: str = "",
        phase: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        text = str(text or "").strip()
        if not text:
            return
        segment = self._find_segment(kind, item_id)
        if segment:
            segment.text = f"{segment.text.strip()}\n\n{text}".strip()
            if phase:
                segment.phase = phase
            if metadata:
                segment.metadata.update(metadata)
            return
        self.segments.append(
            CodexOutputSegment(kind, text, item_id, phase, metadata or {})
        )

    def set_segment(
        self,
        kind: str,
        text: str,
        item_id: str = "",
        phase: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        segment = self._find_or_reclassify_segment(kind, item_id)
        if segment:
            segment.text = str(text or "")
            if phase:
                segment.phase = phase
            if metadata is not None:
                segment.metadata = metadata.copy()
            return
        self.segments.append(
            CodexOutputSegment(kind, str(text or ""), item_id, phase, metadata or {})
        )

    def text_for_kind(self, kind: str) -> str:
        return "\n\n".join(
            segment.text.strip()
            for segment in self.segments
            if segment.kind == kind and segment.text.strip()
        )

    def to_snapshot(self) -> dict[str, Any]:
        final_answer = self.text_for_kind("final_answer")
        return {
            "segments": [
                segment.to_dict() for segment in self.segments if segment.text.strip()
            ],
            "output": final_answer,
            "commentary": self.text_for_kind("commentary"),
            "plan": self.text_for_kind("plan"),
            "reasoning_summary": self.text_for_kind("reasoning_summary"),
            "approval_request": self.text_for_kind("approval_request"),
            "errors": self.text_for_kind("error"),
        }

    def _find_segment(self, kind: str, item_id: str = "") -> CodexOutputSegment | None:
        for segment in self.segments:
            if segment.kind != kind:
                continue
            if item_id:
                if segment.item_id == item_id:
                    return segment
            elif not segment.item_id:
                return segment
        return None

    def _find_segment_by_item_id(self, item_id: str) -> CodexOutputSegment | None:
        for segment in self.segments:
            if segment.item_id == item_id:
                return segment
        return None

    def _find_or_reclassify_segment(
        self, kind: str, item_id: str = ""
    ) -> CodexOutputSegment | None:
        segment = self._find_segment(kind, item_id)
        if segment or not item_id:
            return segment
        segment = self._find_segment_by_item_id(item_id)
        if segment:
            segment.kind = kind
        return segment


OutputState = CodexTurnOutput
OutputSnapshot = dict[str, Any]
OutputCallback = Callable[[OutputSnapshot], None]


@dataclass(frozen=True)
class AppServerMessageClassification:
    kind: str
    method: str = ""


class CodexClient:
    WS_MAX_SIZE = 16 * 1024 * 1024
    TURN_INACTIVITY_TIMEOUT_SECONDS = 180.0

    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _connect_ws(self, websockets: Any) -> Any:
        return websockets.connect(
            self.base_url, open_timeout=self.timeout, max_size=self.WS_MAX_SIZE
        )

    def _empty_output_parts(self) -> OutputState:
        return CodexTurnOutput()

    def _output_parts_snapshot(self, parts: OutputState) -> OutputSnapshot:
        return parts.to_snapshot()

    def _fallback_output_text(self, parts: OutputState) -> str:
        snapshot = self._output_parts_snapshot(parts)
        return (
            snapshot["output"]
            or snapshot["errors"]
            or snapshot["commentary"]
            or snapshot["plan"]
            or snapshot["reasoning_summary"]
        )

    def status(self) -> ConnectionStatus:
        if not self.base_url:
            return ConnectionStatus(
                False, "Not configured", "Codex App Server URL is not set."
            )
        if not self.base_url.startswith(("ws://", "wss://")):
            return ConnectionStatus(
                False, "Invalid URL", "Codex App Server URL must use ws:// or wss://."
            )
        if self._ws_available():
            return ConnectionStatus(
                True, "Connected", f"{self.base_url} responded to WebSocket RPC."
            )
        return ConnectionStatus(
            False,
            "Disconnected",
            "Could not connect to WebSocket RPC. Check the App Server URL.",
        )

    def _ws_available(self) -> bool:
        try:
            return asyncio.run(self._ws_available_async())
        except Exception:
            return False

    async def _ws_available_async(self) -> bool:
        try:
            import websockets
        except ModuleNotFoundError:
            return False

        try:
            async with self._connect_ws(websockets) as websocket:
                output: list[str] = []
                approvals: list[dict[str, Any]] = []
                await self._rpc_call(
                    websocket,
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "codex-nomad-surface",
                            "title": "Codex Nomad Surface",
                            "version": "0.1.0",
                        },
                        "capabilities": {"experimentalApi": True},
                    },
                    output,
                    approvals,
                )
                return True
        except Exception:
            return False

    def start_chat_turn(
        self,
        project_path: str,
        prompt: str,
        thread_id: str | None,
        thread_overrides: dict[str, Any] | None = None,
        turn_overrides: dict[str, Any] | None = None,
        approval_policy: str | None = None,
        output_callback: OutputCallback | None = None,
    ) -> dict[str, Any]:
        if not self.base_url.startswith(("ws://", "wss://")):
            return {
                "ok": False,
                "output": "Codex App Server URL must use ws:// or wss://.",
            }
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(
                self._start_chat_turn_ws(
                    project_path,
                    prompt,
                    thread_id,
                    thread_overrides,
                    turn_overrides,
                    approval_policy,
                    output_callback,
                )
            )
            runtime = result.get("runtime")
            if runtime:
                runtime["loop"] = loop
            else:
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()
            return result
        finally:
            asyncio.set_event_loop(None)

    def respond_chat_turn(
        self,
        runtime: dict[str, Any],
        approval: dict[str, Any],
        decision: str,
        output_callback: OutputCallback | None = None,
    ) -> dict[str, Any]:
        loop = runtime.get("loop")
        if not loop:
            return {"ok": False, "output": "Approval connection was not found."}
        if runtime.get("approval_response_in_progress") or loop.is_running():
            return {
                "ok": False,
                "status": "duplicate_approval_response",
                "output": "Approval response is already being processed.",
                "runtime": runtime,
            }
        if loop.is_closed():
            return {"ok": False, "output": "Approval connection was already closed."}
        runtime["approval_response_in_progress"] = True
        try:
            asyncio.set_event_loop(loop)
            runtime["output_callback"] = output_callback
            result = loop.run_until_complete(
                self._respond_chat_turn_ws(runtime, approval, decision)
            )
            if not result.get("runtime"):
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()
            return result
        finally:
            runtime["approval_response_in_progress"] = False
            asyncio.set_event_loop(None)

    def close_chat_turn(self, runtime: dict[str, Any]) -> None:
        loop = runtime.get("loop")
        if not loop:
            return
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._close_chat_turn_ws(runtime))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
        finally:
            asyncio.set_event_loop(None)

    def list_threads(self) -> list[CodexThread]:
        if not self.base_url.startswith(("ws://", "wss://")):
            return []
        return asyncio.run(self._list_threads_ws())

    def read_thread_messages(
        self, thread_id: str, limit: int = 40, cursor: str | None = None
    ) -> CodexThreadMessages:
        if not thread_id or not self.base_url.startswith(("ws://", "wss://")):
            return CodexThreadMessages([])
        return asyncio.run(self._read_thread_messages_ws(thread_id, limit, cursor))

    def read_thread_runtime_info(self, thread_id: str, cwd: str) -> dict[str, Any]:
        if (
            not thread_id
            or not cwd
            or not self.base_url.startswith(("ws://", "wss://"))
        ):
            return {}
        return asyncio.run(self._read_thread_runtime_info_ws(thread_id, cwd))

    def list_skills(self, cwd: str, force_reload: bool = False) -> list[dict[str, Any]]:
        if not cwd or not self.base_url.startswith(("ws://", "wss://")):
            return []
        return asyncio.run(self._list_skills_ws(cwd, force_reload))

    def read_config(self) -> dict[str, Any]:
        if not self.base_url.startswith(("ws://", "wss://")):
            return {}
        return asyncio.run(self._read_config_ws())

    async def _read_config_ws(self) -> dict[str, Any]:
        try:
            import websockets
        except ModuleNotFoundError:
            return {}

        try:
            async with self._connect_ws(websockets) as websocket:
                output: list[str] = []
                approvals: list[dict[str, Any]] = []
                await self._rpc_call(
                    websocket,
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "codex-nomad-surface",
                            "title": "Codex Nomad Surface",
                            "version": "0.1.0",
                        },
                        "capabilities": {"experimentalApi": True},
                    },
                    output,
                    approvals,
                )
                raw = await self._rpc_call(
                    websocket, "config/read", {}, output, approvals
                )
                config = raw.get("config") if isinstance(raw, dict) else None
                return config if isinstance(config, dict) else {}
        except Exception:
            return {}

    def list_models(self, include_hidden: bool = False) -> list[dict[str, Any]]:
        if not self.base_url.startswith(("ws://", "wss://")):
            return []
        return asyncio.run(self._list_models_ws(include_hidden))

    async def _list_models_ws(self, include_hidden: bool) -> list[dict[str, Any]]:
        try:
            import websockets
        except ModuleNotFoundError:
            return []

        try:
            async with self._connect_ws(websockets) as websocket:
                output: list[str] = []
                approvals: list[dict[str, Any]] = []
                await self._rpc_call(
                    websocket,
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "codex-nomad-surface",
                            "title": "Codex Nomad Surface",
                            "version": "0.1.0",
                        },
                        "capabilities": {"experimentalApi": True},
                    },
                    output,
                    approvals,
                )
                raw = await self._rpc_call(
                    websocket,
                    "model/list",
                    {"includeHidden": include_hidden},
                    output,
                    approvals,
                )
                return self._parse_models(raw)
        except Exception:
            return []

    def _parse_models(self, raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, dict):
            return []
        candidates = raw.get("models") or raw.get("data") or raw.get("items")
        if not isinstance(candidates, list):
            return []
        return [item for item in candidates if isinstance(item, dict)]

    async def _list_skills_ws(
        self, cwd: str, force_reload: bool
    ) -> list[dict[str, Any]]:
        try:
            import websockets
        except ModuleNotFoundError:
            return []

        try:
            async with self._connect_ws(websockets) as websocket:
                output: list[str] = []
                approvals: list[dict[str, Any]] = []
                await self._rpc_call(
                    websocket,
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "codex-nomad-surface",
                            "title": "Codex Nomad Surface",
                            "version": "0.1.0",
                        },
                        "capabilities": {"experimentalApi": True},
                    },
                    output,
                    approvals,
                )
                raw = await self._rpc_call(
                    websocket,
                    "skills/list",
                    {"cwds": [cwd], "forceReload": force_reload},
                    output,
                    approvals,
                )
                return self._parse_skills_list_result(raw, cwd)
        except Exception:
            return []

    def _parse_skills_list_result(self, raw: Any, cwd: str) -> list[dict[str, Any]]:
        if not isinstance(raw, dict):
            return []
        data = raw.get("data")
        if not isinstance(data, list):
            return []

        for item in data:
            if not isinstance(item, dict) or str(item.get("cwd") or "") != cwd:
                continue
            skills = item.get("skills")
            if isinstance(skills, list):
                return [skill for skill in skills if isinstance(skill, dict)]
        return []

    async def _list_threads_ws(self) -> list[CodexThread]:
        try:
            import websockets
        except ModuleNotFoundError:
            return []

        try:
            async with self._connect_ws(websockets) as websocket:
                output: list[str] = []
                approvals: list[dict[str, Any]] = []
                await self._rpc_call(
                    websocket,
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "codex-nomad-surface",
                            "title": "Codex Nomad Surface",
                            "version": "0.1.0",
                        },
                        "capabilities": {"experimentalApi": True},
                    },
                    output,
                    approvals,
                )
                raw = await self._rpc_call(
                    websocket, "thread/list", {}, output, approvals
                )
                return self._parse_threads(raw)
        except Exception:
            return []

    async def _read_thread_messages_ws(
        self, thread_id: str, limit: int, cursor: str | None
    ) -> CodexThreadMessages:
        try:
            import websockets
        except ModuleNotFoundError:
            return CodexThreadMessages([])

        try:
            async with self._connect_ws(websockets) as websocket:
                output: list[str] = []
                approvals: list[dict[str, Any]] = []
                await self._rpc_call(
                    websocket,
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "codex-nomad-surface",
                            "title": "Codex Nomad Surface",
                            "version": "0.1.0",
                        },
                        "capabilities": {"experimentalApi": True},
                    },
                    output,
                    approvals,
                )
                params: dict[str, Any] = {
                    "threadId": thread_id,
                    "limit": max(1, limit),
                    "sortDirection": "desc",
                }
                if cursor:
                    params["cursor"] = cursor
                raw = await self._rpc_call(
                    websocket, "thread/turns/list", params, output, approvals
                )
        except Exception:
            return CodexThreadMessages([])

        return self._thread_messages_from_turns_list(raw)

    async def _read_thread_runtime_info_ws(
        self, thread_id: str, cwd: str
    ) -> dict[str, Any]:
        try:
            import websockets
        except ModuleNotFoundError:
            return {}

        try:
            async with self._connect_ws(websockets) as websocket:
                output: list[str] = []
                approvals: list[dict[str, Any]] = []
                await self._rpc_call(
                    websocket,
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "codex-nomad-surface",
                            "title": "Codex Nomad Surface",
                            "version": "0.1.0",
                        },
                        "capabilities": {"experimentalApi": True},
                    },
                    output,
                    approvals,
                )
                raw = await self._rpc_call(
                    websocket,
                    "thread/resume",
                    {
                        "threadId": thread_id,
                        "cwd": cwd,
                        "persistExtendedHistory": True,
                    },
                    output,
                    approvals,
                )
                await self._rpc_call(
                    websocket,
                    "thread/unsubscribe",
                    {"threadId": thread_id},
                    output,
                    approvals,
                )
        except Exception:
            return {}

        return raw if isinstance(raw, dict) else {}

    def _approval_response_result(
        self, approval: dict[str, Any], decision: str
    ) -> dict[str, Any]:
        method = str(approval.get("method") or approval.get("title") or "")
        params = (
            approval.get("params") if isinstance(approval.get("params"), dict) else {}
        )
        approved = decision in {"approve", "approveForThread"} or decision.startswith(
            "permissionScope:"
        )
        if decision.startswith("responseJson:"):
            try:
                response = json.loads(decision.split(":", 1)[1])
            except (TypeError, ValueError, json.JSONDecodeError):
                return {}
            return response if isinstance(response, dict) else {}
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            if decision.startswith("decision:"):
                return {"decision": decision.split(":", 1)[1]}
            if decision.startswith("decisionJson:"):
                try:
                    return {"decision": json.loads(decision.split(":", 1)[1])}
                except (TypeError, ValueError, json.JSONDecodeError):
                    return {"decision": "decline"}
            return {"decision": "accept" if approved else "decline"}
        if method == "item/permissions/requestApproval":
            if approved:
                permissions = (
                    params.get("permissions") if isinstance(params, dict) else {}
                )
                if decision.startswith("permissionScope:"):
                    scope = decision.split(":", 1)[1]
                else:
                    scope = "thread" if decision == "approveForThread" else "turn"
                return {"permissions": permissions or {}, "scope": scope}
            return {"permissions": {}, "scope": "turn"}
        if method in {"execCommandApproval", "applyPatchApproval"}:
            return {"decision": "approved" if approved else "denied"}
        if self._is_mcp_elicitation_method(method):
            if approved:
                return {"action": "accept", "content": {}}
            return {"action": "decline"}
        if self._is_tool_request_user_input_method(method):
            return self._tool_request_user_input_response(approval, decision)
        if approval.get("kind") == "generic_user_response_request":
            return {"accepted": approved}
        return {"decision": "accept" if approved else "decline"}

    def _classify_app_server_message(
        self, message: dict[str, Any]
    ) -> AppServerMessageClassification:
        method = str(message.get("method") or "")
        if not method:
            return AppServerMessageClassification("unknown_observed", method)
        recognized_summary = self._recognized_event_summary(message)
        if recognized_summary is not None:
            kind = "known_output" if recognized_summary else "known_silent"
            return AppServerMessageClassification(kind, method)
        if self._is_approval_request_message(message):
            if self._message_response_id(message) is not None:
                return AppServerMessageClassification("response_required", method)
            return AppServerMessageClassification("unknown_observed", method)
        if message.get("id") is not None:
            return AppServerMessageClassification("response_required", method)
        if self._is_known_output_message(message):
            return AppServerMessageClassification("known_output", method)
        return AppServerMessageClassification("unknown_observed", method)

    def _is_approval_request_message(self, message: dict[str, Any]) -> bool:
        method = str(message.get("method") or "")
        return (
            "requestApproval" in method
            or self._is_mcp_elicitation_method(method)
            or self._is_tool_request_user_input_method(method)
        )

    def _is_user_response_request_message(self, message: dict[str, Any]) -> bool:
        return self._classify_app_server_message(message).kind == "response_required"

    def _message_response_id(self, message: dict[str, Any]) -> Any:
        if message.get("id") is not None:
            return message.get("id")
        params = message.get("params")
        if not isinstance(params, dict):
            return None
        return self._response_id_from_mapping(params)

    def _response_id_from_mapping(self, mapping: dict[str, Any]) -> Any:
        direct_keys = (
            "id",
            "requestId",
            "request_id",
            "approvalId",
            "approval_id",
            "elicitationId",
            "elicitation_id",
            "serverRequestId",
            "server_request_id",
        )
        for key in direct_keys:
            if mapping.get(key) is not None:
                return mapping.get(key)
        for value in mapping.values():
            if not isinstance(value, dict):
                continue
            for key in direct_keys:
                if value.get(key) is not None:
                    return value.get(key)
        return None

    def _is_known_output_message(self, message: dict[str, Any]) -> bool:
        method = str(message.get("method") or "")
        return method in {
            "item/started",
            "item/agentMessage/delta",
            "item/plan/delta",
            "item/reasoning/summaryTextDelta",
            "item/reasoning/summaryPartAdded",
            "item/completed",
            "error",
            "turn/completed",
        }

    def _known_request_options(
        self, method: str, params: dict[str, Any], questions: list[dict[str, Any]]
    ) -> list[dict[str, str]]:
        if self._is_tool_request_user_input_method(method):
            return self._tool_request_user_input_button_options(questions)
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            return self._available_decision_options(params)
        if method == "item/permissions/requestApproval":
            return self._permission_request_options(params)
        return []

    def _available_decision_options(self, params: dict[str, Any]) -> list[dict[str, str]]:
        decisions = params.get("availableDecisions") if isinstance(params, dict) else None
        if not isinstance(decisions, list):
            return []
        options: list[dict[str, str]] = []
        for decision in decisions:
            label = self._available_decision_label(decision)
            if not label:
                continue
            if isinstance(decision, str):
                value = f"decision:{decision}"
            else:
                value = f"decisionJson:{self._compact_json(decision)}"
            options.append({"label": label, "decision": value})
        return options

    def _available_decision_label(self, decision: Any) -> str:
        if isinstance(decision, str):
            labels = {
                "accept": "Accept",
                "acceptForSession": "Accept for session",
                "decline": "Decline",
                "cancel": "Cancel",
            }
            return labels.get(decision, decision)
        if isinstance(decision, dict) and len(decision) == 1:
            key = next(iter(decision.keys()))
            labels = {
                "acceptWithExecpolicyAmendment": "Accept with permission change",
            }
            return labels.get(str(key), str(key))
        return str(decision or "").strip()

    def _permission_request_options(
        self, params: dict[str, Any]
    ) -> list[dict[str, str]]:
        options = self._explicit_response_options(params)
        if options:
            if not self._has_negative_response_option(options):
                options.append({"label": "Decline", "decision": "reject"})
            return options
        return self._scope_response_options(params)

    def _explicit_response_options(
        self, params: dict[str, Any]
    ) -> list[dict[str, str]]:
        candidates = self._first_list_value(
            params,
            (
                "availableResponses",
                "responseOptions",
                "availableOptions",
                "options",
                "availableChoices",
                "choices",
            ),
        )
        if not candidates:
            return []

        options: list[dict[str, str]] = []
        for index, candidate in enumerate(candidates):
            if not isinstance(candidate, dict):
                continue
            label = self._option_label(candidate, f"Option {index + 1}")
            response = self._option_response_payload(candidate)
            if response is not None:
                options.append(
                    {
                        "label": label,
                        "decision": f"responseJson:{self._compact_json(response)}",
                    }
                )
        return options

    def _scope_response_options(self, params: dict[str, Any]) -> list[dict[str, str]]:
        scopes = self._first_list_value(
            params,
            (
                "availableScopes",
                "scopeOptions",
                "availablePermissionScopes",
                "permissionScopes",
                "scopes",
            ),
        )
        if not scopes:
            return []

        options: list[dict[str, str]] = []
        for scope in scopes:
            value = self._scope_value(scope)
            if not value:
                continue
            options.append(
                {
                    "label": self._scope_label(scope, value),
                    "decision": f"permissionScope:{value}",
                }
            )
        options.append({"label": "Decline", "decision": "reject"})
        return options

    def _first_list_value(
        self, mapping: dict[str, Any], keys: tuple[str, ...]
    ) -> list[Any]:
        for key in keys:
            value = mapping.get(key)
            if isinstance(value, list):
                return value
        return []

    def _option_label(self, option: dict[str, Any], fallback: str) -> str:
        for key in ("label", "title", "name", "description", "value", "scope"):
            value = option.get(key)
            if value is not None:
                text = str(value).strip()
                if text:
                    return text
        return fallback

    def _option_response_payload(self, option: dict[str, Any]) -> Any | None:
        for key in ("response", "result", "payload", "answer"):
            value = option.get(key)
            if isinstance(value, dict):
                return value
        return None

    def _option_scope(self, option: dict[str, Any]) -> str:
        for key in ("scope", "value", "id"):
            value = option.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _has_negative_response_option(self, options: list[dict[str, str]]) -> bool:
        negative_words = ("decline", "reject", "deny", "cancel")
        for option in options:
            label = str(option.get("label") or "").casefold()
            decision = str(option.get("decision") or "").casefold()
            if any(word in label or word in decision for word in negative_words):
                return True
        return False

    def _scope_value(self, scope: Any) -> str:
        if isinstance(scope, str):
            return scope.strip()
        if isinstance(scope, dict):
            return self._option_scope(scope)
        return ""

    def _scope_label(self, scope: Any, value: str) -> str:
        if isinstance(scope, dict):
            return self._option_label(scope, value)
        labels = {
            "turn": "Allow once",
            "thread": "Allow in this thread",
            "session": "Allow for session",
        }
        return labels.get(value, value)

    def _compact_json(self, value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            return json.dumps(str(value), ensure_ascii=False)

    def _is_mcp_elicitation_method(self, method: str) -> bool:
        return method in {
            "mcpServer/elicitation/request",
            "elicitation/create",
        } or method.endswith("/elicitation/request")

    def _is_tool_request_user_input_method(self, method: str) -> bool:
        return method in {"tool/requestUserInput", "item/tool/requestUserInput"}

    async def _start_chat_turn_ws(
        self,
        project_path: str,
        prompt: str,
        thread_id: str | None,
        thread_overrides: dict[str, Any] | None = None,
        turn_overrides: dict[str, Any] | None = None,
        approval_policy: str | None = None,
        output_callback: OutputCallback | None = None,
    ) -> dict[str, Any]:
        try:
            import websockets
        except ModuleNotFoundError:
            return {
                "ok": False,
                "output": "`websockets` is not installed.",
            }

        output_parts = self._empty_output_parts()
        approvals: list[dict[str, Any]] = []
        runtime: dict[str, Any] = {
            "output_parts": output_parts,
            "stream_items": {},
            "approvals": approvals,
            "thread_id": thread_id,
            "output_callback": output_callback,
        }
        thread_overrides = thread_overrides or {}
        turn_overrides = turn_overrides or {}
        try:
            websocket = await self._connect_ws(websockets)
            runtime["websocket"] = websocket

            try:

                async def handle_approval_message(message: dict[str, Any]) -> None:
                    runtime["approval"] = self._approval_from_message(message)
                    approvals.append(runtime["approval"])
                    raise ApprovalRequired

                await self._rpc_call(
                    websocket,
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "codex-nomad-surface",
                            "title": "Codex Nomad Surface",
                            "version": "0.1.0",
                        },
                        "capabilities": {"experimentalApi": True},
                    },
                    output_parts,
                    approvals,
                    output_callback,
                    handle_approval_message,
                    stream_items=runtime["stream_items"],
                )
                if thread_id:
                    resume_params: dict[str, Any] = {
                        "threadId": thread_id,
                        "cwd": project_path,
                        "persistExtendedHistory": True,
                    }
                    if approval_policy:
                        resume_params["approvalPolicy"] = approval_policy
                    resume_params.update(thread_overrides)
                    thread_result = await self._rpc_call(
                        websocket,
                        "thread/resume",
                        resume_params,
                        output_parts,
                        approvals,
                        output_callback,
                        handle_approval_message,
                        runtime["stream_items"],
                    )
                else:
                    start_params: dict[str, Any] = {
                        "cwd": project_path,
                        "ephemeral": False,
                        "sessionStartSource": "startup",
                        "experimentalRawEvents": False,
                        "persistExtendedHistory": True,
                    }
                    if approval_policy:
                        start_params["approvalPolicy"] = approval_policy
                    start_params.update(thread_overrides)
                    thread_result = await self._rpc_call(
                        websocket,
                        "thread/start",
                        start_params,
                        output_parts,
                        approvals,
                        output_callback,
                        handle_approval_message,
                        runtime["stream_items"],
                    )
                thread_id = thread_result["thread"]["id"]
                runtime["thread_id"] = thread_id
                turn_params: dict[str, Any] = {
                    "threadId": thread_id,
                    "cwd": project_path,
                    "input": [
                        {
                            "type": "text",
                            "text": prompt,
                            "text_elements": [],
                        }
                    ],
                }
                if approval_policy:
                    turn_params["approvalPolicy"] = approval_policy
                await self._rpc_call(
                    websocket,
                    "turn/start",
                    turn_params | turn_overrides,
                    output_parts,
                    approvals,
                    output_callback,
                    handle_approval_message,
                    runtime["stream_items"],
                )
                return await self._collect_chat_turn_ws(runtime)
            except ApprovalRequired:
                return self._approval_result(runtime)
        except ApprovalRequired:
            return self._approval_result(runtime)
        except Exception as exc:
            await self._close_chat_turn_ws(runtime)
            text_output = self._fallback_output_text(output_parts)
            if text_output:
                text_output = f"{text_output}\n\n[send/receive error] {exc}"
            else:
                text_output = f"[send/receive error] {exc}"
            output_parts.append_block("error", text_output)
            return {
                "ok": False,
                "thread_id": thread_id,
                "output": self._output_parts_snapshot(output_parts)["output"]
                or text_output,
                "output_parts": self._output_parts_snapshot(output_parts),
                "approvals": approvals,
            }

    async def _respond_chat_turn_ws(
        self, runtime: dict[str, Any], approval: dict[str, Any], decision: str
    ) -> dict[str, Any]:
        websocket = runtime.get("websocket")
        if not websocket:
            return {"ok": False, "output": "Approval connection was not found."}
        result = self._approval_response_result(approval, decision)
        try:
            await websocket.send(
                json.dumps(
                    {"id": approval.get("id"), "result": result}, ensure_ascii=False
                )
            )
            runtime.pop("approval", None)
            return await self._collect_chat_turn_ws(runtime)
        except Exception as exc:
            await self._close_chat_turn_ws(runtime)
            output_parts = runtime.get("output_parts") or self._empty_output_parts()
            output = self._fallback_output_text(output_parts)
            if output:
                output = f"{output}\n\n[send/receive error] {exc}"
            else:
                output = f"[send/receive error] {exc}"
            output_parts.append_block("error", output)
            return {
                "ok": False,
                "thread_id": runtime.get("thread_id"),
                "output": self._output_parts_snapshot(output_parts)["output"] or output,
                "output_parts": self._output_parts_snapshot(output_parts),
                "approvals": runtime.get("approvals") or [],
            }

    async def _collect_chat_turn_ws(self, runtime: dict[str, Any]) -> dict[str, Any]:
        websocket = runtime["websocket"]
        thread_id = runtime.get("thread_id")
        output_parts = runtime["output_parts"]
        stream_items = runtime.setdefault("stream_items", {})
        approvals = runtime["approvals"]
        output_callback = runtime.get("output_callback")
        loop = asyncio.get_running_loop()
        last_activity = loop.time()
        while True:
            inactive_for = loop.time() - last_activity
            if inactive_for >= self.TURN_INACTIVITY_TIMEOUT_SECONDS:
                break
            remaining = max(0.1, self.TURN_INACTIVITY_TIMEOUT_SECONDS - inactive_for)
            try:
                raw_message = await asyncio.wait_for(
                    websocket.recv(), timeout=min(30.0, remaining)
                )
            except asyncio.TimeoutError:
                continue

            last_activity = loop.time()
            message = json.loads(raw_message)
            method = message.get("method")
            params = message.get("params") or {}
            if self._is_user_response_request_message(message):
                approval = self._approval_from_message(message)
                approvals.append(approval)
                runtime["approval"] = approval
                return self._approval_result(runtime)

            changed = self._update_output_parts(
                message, output_parts, approvals, stream_items
            )
            if changed:
                if output_callback:
                    output_callback(self._output_parts_snapshot(output_parts))
            if method == "turn/completed" and params.get("threadId") == thread_id:
                await self._close_chat_turn_ws(runtime)
                snapshot = self._output_parts_snapshot(output_parts)
                return {
                    "ok": True,
                    "thread_id": thread_id,
                    "output": snapshot["output"],
                    "output_parts": snapshot,
                    "approvals": approvals,
                }
        await self._close_chat_turn_ws(runtime)
        output_parts.append_block(
            "error",
            "Codex turn did not receive activity for 180 seconds.",
        )
        snapshot = self._output_parts_snapshot(output_parts)
        return {
            "ok": False,
            "thread_id": thread_id,
            "output": snapshot["output"],
            "output_parts": snapshot,
            "approvals": approvals,
        }

    def _approval_result(self, runtime: dict[str, Any]) -> dict[str, Any]:
        runtime.pop("output_callback", None)
        output_parts = runtime.get("output_parts") or self._empty_output_parts()
        snapshot = self._output_parts_snapshot(output_parts)
        return {
            "ok": False,
            "status": "approval",
            "thread_id": runtime.get("thread_id"),
            "output": snapshot["output"],
            "output_parts": snapshot,
            "approval": runtime.get("approval"),
            "approvals": runtime.get("approvals") or [],
            "runtime": runtime,
        }

    async def _close_chat_turn_ws(self, runtime: dict[str, Any]) -> None:
        websocket = runtime.pop("websocket", None)
        runtime.pop("loop", None)
        if websocket:
            await websocket.close()

    def _parse_threads(self, raw: Any) -> list[CodexThread]:
        if isinstance(raw, dict):
            raw_threads = raw.get("data") or raw.get("threads") or []
        elif isinstance(raw, list):
            raw_threads = raw
        else:
            raw_threads = []

        threads: list[CodexThread] = []
        for item in raw_threads:
            if not isinstance(item, dict):
                continue
            thread = CodexThread.from_dict(item)
            if thread.id and thread.cwd:
                threads.append(thread)
        return sorted(
            threads,
            key=lambda thread: thread.updated_at or thread.created_at,
            reverse=True,
        )

    def _thread_messages_from_turns_list(self, raw: Any) -> CodexThreadMessages:
        if not isinstance(raw, dict):
            return CodexThreadMessages([])

        raw_turns = raw.get("data") or raw.get("turns") or []
        if not isinstance(raw_turns, list):
            raw_turns = []

        messages: list[dict[str, Any]] = []
        for turn in reversed(raw_turns):
            messages.extend(self._messages_from_turn(turn))

        next_cursor = raw.get("nextCursor")
        return CodexThreadMessages(
            messages=messages,
            cursor=str(next_cursor) if next_cursor else None,
            has_older=bool(next_cursor),
        )

    def _messages_from_turn(self, turn: Any) -> list[dict[str, Any]]:
        if not isinstance(turn, dict):
            return []

        items = turn.get("items")
        if isinstance(items, list):
            messages: list[dict[str, Any]] = []
            assistant_parts = self._empty_output_parts()
            for item in items:
                message = self._thread_message_from_item(item)
                if message:
                    messages.append(message)
                    continue
                self._update_output_parts_from_item(item, assistant_parts)
            assistant_snapshot = self._output_parts_snapshot(assistant_parts)
            if any(assistant_snapshot.values()):
                messages.append(
                    {
                        "role": "assistant",
                        "content": assistant_snapshot["output"],
                        "metadata": {"codex_output": assistant_snapshot},
                    }
                )
            return messages

        return []

    def _thread_message_from_item(self, item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None

        item_type = str(item.get("type") or "")
        if item_type == "userMessage":
            content = self._content_text(item.get("content")).strip()
            if not content:
                return None
            return {"role": "user", "content": content}

        return None

    def _update_output_parts_from_item(self, item: Any, parts: OutputState) -> bool:
        if not isinstance(item, dict):
            return False

        item_type = str(item.get("type") or "")
        item_id = str(item.get("id") or "")
        if item_type == "agentMessage":
            text = str(item.get("text") or "")
            if text:
                phase = str(item.get("phase") or "")
                kind = "commentary" if phase == "commentary" else "final_answer"
                parts.set_segment(kind, text, item_id, phase)
                return True
        if item_type == "plan":
            text = str(item.get("text") or "")
            if text:
                parts.set_segment("plan", text, item_id)
                return True
        if item_type == "reasoning":
            text = self._reasoning_summary_text(item.get("summary"))
            if text:
                parts.set_segment("reasoning_summary", text, item_id)
            return True
        recognized_summary = self._recognized_item_summary(item)
        if recognized_summary:
            parts.append_block("operation_event", recognized_summary, item_id)
            return True
        if item_type and item_type != "userMessage":
            parts.append_block(
                "other_event",
                f"Unrecognized item: `{item_type}`",
            )
            return True
        return False

    def _recognized_item_summary(self, item: dict[str, Any]) -> str:
        item_type = str(item.get("type") or "")
        renderers = {
            "commandExecution": self._command_execution_item_summary,
            "fileChange": self._file_change_item_summary,
            "mcpToolCall": self._mcp_tool_call_item_summary,
        }
        renderer = renderers.get(item_type)
        return renderer(item) if renderer else ""

    def _command_execution_item_summary(self, item: dict[str, Any]) -> str:
        lines = ["**Command execution**"]
        command = self._format_command(item.get("command"))
        if command:
            lines.append(f"- Command: `{command}`")
        cwd = str(item.get("cwd") or "").strip()
        if cwd:
            lines.append(f"- CWD: `{cwd}`")
        status = str(item.get("status") or "").strip()
        if status:
            lines.append(f"- Status: `{status}`")
        if item.get("exitCode") is not None:
            lines.append(f"- Exit code: `{item.get('exitCode')}`")
        if item.get("durationMs") is not None:
            lines.append(f"- Duration: `{item.get('durationMs')} ms`")
        output = self._first_line(item.get("aggregatedOutput"))
        if output:
            lines.append(f"- Output: {output}")
        return "\n".join(lines)

    def _file_change_item_summary(self, item: dict[str, Any]) -> str:
        lines = ["**File change**"]
        status = str(item.get("status") or "").strip()
        if status:
            lines.append(f"- Status: `{status}`")

        changes = item.get("changes")
        if not isinstance(changes, list):
            changes = []
        lines.append(f"- Changes: `{len(changes)}`")
        for change in changes[:5]:
            if not isinstance(change, dict):
                continue
            path = str(change.get("path") or "").strip()
            kind = str(change.get("kind") or "").strip()
            if path and kind:
                lines.append(f"- `{path}` ({kind})")
            elif path:
                lines.append(f"- `{path}`")
        if len(changes) > 5:
            lines.append(f"- ... and {len(changes) - 5} more")
        return "\n".join(lines)

    def _mcp_tool_call_item_summary(self, item: dict[str, Any]) -> str:
        lines = ["**MCP tool call**"]
        server = str(item.get("server") or "").strip()
        if server:
            lines.append(f"- Server: `{server}`")
        tool = str(item.get("tool") or "").strip()
        if tool:
            lines.append(f"- Tool: `{tool}`")
        status = str(item.get("status") or "").strip()
        if status:
            lines.append(f"- Status: `{status}`")
        arguments = self._json_first_line(item.get("arguments"))
        if arguments:
            lines.append(f"- Arguments: `{arguments}`")
        error = self._first_line(item.get("error"))
        if error:
            lines.append(f"- Error: {error}")
        return "\n".join(lines)

    def _format_command(self, command: Any) -> str:
        if isinstance(command, list):
            return " ".join(shlex.quote(str(part)) for part in command)
        return str(command or "").strip()

    def _json_first_line(self, value: Any, max_chars: int = 240) -> str:
        if value in (None, ""):
            return ""
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            text = str(value)
        return self._first_line(text, max_chars)

    def _first_line(self, value: Any, max_chars: int = 240) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        line = next((part.strip() for part in text.splitlines() if part.strip()), "")
        if len(line) <= max_chars:
            return line
        return f"{line[:max_chars].rstrip()}..."

    def _reasoning_summary_text(self, summary: Any) -> str:
        if isinstance(summary, str):
            return summary
        if not isinstance(summary, list):
            return ""

        parts: list[str] = []
        for item in summary:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("summary") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n\n".join(part.strip() for part in parts if part and part.strip())

    def _content_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""

        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(part for part in parts if part)

    def _approval_from_message(self, message: dict[str, Any]) -> dict[str, Any]:
        method = str(message.get("method") or "")
        params = message.get("params") or {}
        questions = (
            self._tool_request_user_input_questions(params)
            if self._is_tool_request_user_input_method(method)
            else []
        )
        kind = (
            "tool_user_input_request"
            if self._is_tool_request_user_input_method(method)
            else "approval_request"
            if self._is_approval_request_message(message)
            else "generic_user_response_request"
        )
        return {
            "id": self._message_response_id(message),
            "kind": kind,
            "method": method,
            "params": params,
            "title": self._tool_request_user_input_title(questions) or method,
            "detail": self._tool_request_user_input_detail(questions)
            if questions
            else self._json_preview(params),
            "questions": questions,
            "options": self._known_request_options(method, params, questions),
        }

    def _tool_request_user_input_questions(
        self, params: dict[str, Any]
    ) -> list[dict[str, Any]]:
        questions = params.get("questions") if isinstance(params, dict) else None
        normalized: list[dict[str, Any]] = []
        iterable: list[tuple[str, Any]]
        if isinstance(questions, dict):
            iterable = [(str(key), value) for key, value in questions.items()]
        elif isinstance(questions, list):
            iterable = [
                (
                    str(
                        question.get("id")
                        or question.get("key")
                        or question.get("name")
                        or index
                    )
                    if isinstance(question, dict)
                    else str(index),
                    question,
                )
                for index, question in enumerate(questions)
            ]
        else:
            return normalized

        for question_id, question in iterable:
            if not isinstance(question, dict):
                continue
            option_labels: list[str] = []
            options = question.get("options")
            if isinstance(options, list):
                for option in options:
                    if isinstance(option, dict):
                        label = str(option.get("label") or option.get("value") or "")
                    else:
                        label = str(option or "")
                    label = label.strip()
                    if label:
                        option_labels.append(label)
            normalized.append(
                {
                    "id": question_id,
                    "header": str(question.get("header") or "").strip(),
                    "question": str(question.get("question") or "").strip(),
                    "isOther": bool(question.get("isOther")),
                    "isSecret": bool(question.get("isSecret")),
                    "options": option_labels,
                }
            )
        return normalized

    def _tool_request_user_input_title(self, questions: list[dict[str, Any]]) -> str:
        for question in questions:
            title = str(question.get("header") or question.get("question") or "")
            if title.strip():
                return title.strip()
        return ""

    def _tool_request_user_input_detail(self, questions: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for question in questions:
            text = str(question.get("question") or question.get("header") or "").strip()
            if text:
                lines.append(text)
            options = question.get("options")
            if isinstance(options, list) and options:
                lines.append(
                    "Options: " + ", ".join(str(option) for option in options)
                )
        return "\n".join(lines)

    def _tool_request_user_input_button_options(
        self, questions: list[dict[str, Any]]
    ) -> list[dict[str, str]]:
        if len(questions) != 1:
            return []
        options = questions[0].get("options")
        if not isinstance(options, list):
            return []
        return [
            {"label": str(label), "decision": f"option:{index}"}
            for index, label in enumerate(options)
            if str(label).strip()
        ]

    def _tool_request_user_input_response(
        self, approval: dict[str, Any], decision: str
    ) -> dict[str, Any]:
        if decision.startswith("answersJson:"):
            try:
                value = json.loads(decision.split(":", 1)[1])
            except (TypeError, ValueError, json.JSONDecodeError):
                return {}
            return value if isinstance(value, dict) else {}

        questions = approval.get("questions")
        if not isinstance(questions, list):
            params = (
                approval.get("params") if isinstance(approval.get("params"), dict) else {}
            )
            questions = self._tool_request_user_input_questions(params)

        response: dict[str, Any] = {}
        for question in questions:
            if not isinstance(question, dict):
                continue
            question_id = str(question.get("id") or "").strip()
            if not question_id:
                continue
            label = self._tool_request_user_input_selected_label(question, decision)
            response[question_id] = {"answers": [label] if label else []}
        return response

    def _tool_request_user_input_selected_label(
        self, question: dict[str, Any], decision: str
    ) -> str:
        options = question.get("options")
        if not isinstance(options, list):
            options = []
        labels = [str(option).strip() for option in options if str(option).strip()]
        if decision.startswith("option:"):
            try:
                index = int(decision.split(":", 1)[1])
            except ValueError:
                index = -1
            if 0 <= index < len(labels):
                return labels[index]
        preferred = ("accept", "approve", "allow", "yes") if decision == "approve" else (
            "decline",
            "reject",
            "deny",
            "no",
            "cancel",
        )
        for label in labels:
            folded = label.casefold()
            if any(word in folded for word in preferred):
                return label
        return labels[0] if labels else (
            "Accept" if decision == "approve" else "Decline"
        )

    def _json_preview(self, value: Any, max_chars: int = 8000) -> str:
        try:
            text = json.dumps(value, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            text = str(value)
        if len(text) <= max_chars:
            return text
        return f"{text[:max_chars].rstrip()}\n... [truncated]"

    def _update_output_parts(
        self,
        message: dict[str, Any],
        parts: OutputState,
        approvals: list[dict[str, Any]],
        stream_items: dict[str, dict[str, str]] | None = None,
    ) -> bool:
        method = message.get("method")
        params = message.get("params") or {}
        if method == "item/started":
            item = params.get("item") if isinstance(params, dict) else None
            if self._update_stream_item(item, parts, stream_items):
                return True
            return self._update_output_parts_from_item(item, parts)
        if method == "item/agentMessage/delta":
            return self._append_agent_message_delta(params, parts, stream_items)
        if method == "item/plan/delta":
            item_id = str(params.get("itemId") or params.get("item_id") or "")
            parts.append_delta("plan", str(params.get("delta") or ""), item_id)
            return True
        if method == "item/reasoning/summaryTextDelta":
            item_id = str(params.get("itemId") or params.get("item_id") or "")
            metadata = {}
            if "summaryIndex" in params:
                metadata["summary_index"] = params.get("summaryIndex")
            parts.append_delta(
                "reasoning_summary",
                str(params.get("delta") or ""),
                item_id,
                metadata=metadata,
            )
            return True
        if method == "item/reasoning/summaryPartAdded":
            item_id = str(params.get("itemId") or params.get("item_id") or "")
            parts.append_delta("reasoning_summary", "\n\n", item_id)
            return True
        if method == "item/completed":
            item = params.get("item") if isinstance(params, dict) else None
            if self._update_stream_item(item, parts, stream_items):
                return True
            return self._update_output_parts_from_item(item, parts)
        if method == "error":
            parts.append_block("error", f"[error] {params.get('message') or params}")
            return True
        classification = self._classify_app_server_message(message)
        if classification.kind == "response_required":
            approvals.append(self._approval_from_message(message))
            text = (
                "An operation requires approval"
                if self._is_approval_request_message(message)
                else "Codex is waiting for a user response"
            )
            parts.append_block("approval_request", text)
            return True
        if classification.kind == "known_silent":
            return True
        if classification.kind == "known_output":
            recognized_summary = self._recognized_event_summary(message)
            if recognized_summary:
                parts.append_block("operation_event", recognized_summary)
                return True
            return False
        if classification.kind == "unknown_observed" and method:
            parts.append_block(
                "other_event",
                f"Unrecognized event: `{method}`",
            )
            return True
        return False

    def _recognized_event_summary(self, message: dict[str, Any]) -> str | None:
        method = str(message.get("method") or "")
        renderers = {
            "thread/started": self._silent_event_summary,
            "thread/status/changed": self._silent_event_summary,
            "turn/started": self._silent_event_summary,
            "mcpServer/startupStatus/updated": self._silent_event_summary,
            "skills/changed": self._silent_event_summary,
            "account/rateLimits/updated": self._silent_event_summary,
            "thread/tokenUsage/updated": self._silent_event_summary,
            "serverRequest/resolved": self._silent_event_summary,
        }
        renderer = renderers.get(method)
        return renderer(message) if renderer else None

    def _silent_event_summary(self, message: dict[str, Any]) -> str:
        return ""

    def _update_stream_item(
        self,
        item: Any,
        parts: OutputState,
        stream_items: dict[str, dict[str, str]] | None,
    ) -> bool:
        if not isinstance(item, dict) or stream_items is None:
            return False
        item_id = str(item.get("id") or "")
        if not item_id:
            return False

        item_type = str(item.get("type") or "")
        if item_type != "agentMessage":
            return False

        stream_item = stream_items.setdefault(
            item_id,
            {"type": item_type, "phase": "", "text": ""},
        )
        stream_item["type"] = item_type
        phase = str(item.get("phase") or "")
        if phase:
            stream_item["phase"] = phase
        if "text" in item:
            stream_item["text"] = str(item.get("text") or "")
            kind = (
                "commentary"
                if stream_item.get("phase") == "commentary"
                else "final_answer"
            )
            parts.set_segment(
                kind, stream_item["text"], item_id, stream_item.get("phase", "")
            )
        return True

    def _append_agent_message_delta(
        self,
        params: Any,
        parts: OutputState,
        stream_items: dict[str, dict[str, str]] | None,
    ) -> bool:
        if not isinstance(params, dict):
            return False
        delta = str(params.get("delta") or "")
        if not delta:
            return False

        item_id = str(params.get("itemId") or params.get("item_id") or "")
        if not item_id or stream_items is None:
            parts.append_delta("final_answer", delta)
            return True

        stream_item = stream_items.setdefault(
            item_id,
            {"type": "agentMessage", "phase": "", "text": ""},
        )
        phase = str(params.get("phase") or "")
        if phase:
            stream_item["phase"] = phase
        stream_item["text"] += delta
        kind = (
            "commentary" if stream_item.get("phase") == "commentary" else "final_answer"
        )
        parts.append_delta(kind, delta, item_id, stream_item.get("phase", ""))
        return True

    async def _rpc_call(
        self,
        websocket: Any,
        method: str,
        params: dict[str, Any],
        output: list[str] | OutputState,
        approvals: list[dict[str, Any]],
        output_callback: OutputCallback | None = None,
        approval_handler: Any | None = None,
        stream_items: dict[str, dict[str, str]] | None = None,
    ) -> Any:
        request_id = str(uuid.uuid4())
        await websocket.send(
            json.dumps(
                {"id": request_id, "method": method, "params": params},
                ensure_ascii=False,
            )
        )
        while True:
            message = json.loads(
                await asyncio.wait_for(
                    websocket.recv(), timeout=max(self.timeout, 30.0)
                )
            )
            if (
                approval_handler
                and self._is_user_response_request_message(message)
                and message.get("id") != request_id
            ):
                await approval_handler(message)
                continue
            if isinstance(output, CodexTurnOutput):
                changed = self._update_output_parts(
                    message, output, approvals, stream_items
                )
                if changed and output_callback:
                    output_callback(self._output_parts_snapshot(output))
            else:
                chunk = self._handle_ws_message(message, output, approvals)
                if chunk and output_callback:
                    output_callback(
                        {
                            "output": "".join(output).strip(),
                            "commentary": "",
                            "plan": "",
                            "reasoning_summary": "",
                            "errors": "",
                        }
                    )
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(
                        message["error"].get("message")
                        or json.dumps(message["error"], ensure_ascii=False)
                    )
                return message.get("result")

    def _handle_ws_message(
        self,
        message: dict[str, Any],
        output: list[str],
        approvals: list[dict[str, Any]],
    ) -> str:
        method = message.get("method")
        params = message.get("params") or {}
        if method == "item/agentMessage/delta":
            chunk = str(params.get("delta") or "")
            output.append(chunk)
            return chunk
        elif method == "item/plan/delta":
            chunk = str(params.get("delta") or "")
            output.append(chunk)
            return chunk
        elif method == "item/reasoning/summaryTextDelta":
            chunk = str(params.get("delta") or "")
            output.append(chunk)
            return chunk
        elif method == "error":
            chunk = f"\n[error] {params.get('message') or params}\n"
            output.append(chunk)
            return chunk
        elif self._is_user_response_request_message(message):
            approvals.append(self._approval_from_message(message))
            return "\n\n[Codex is waiting for a user response]\n"
        return ""

    def _message_chunk(
        self, message: dict[str, Any], approvals: list[dict[str, Any]]
    ) -> str:
        method = message.get("method")
        params = message.get("params") or {}
        if method in {
            "item/agentMessage/delta",
            "item/plan/delta",
            "item/reasoning/summaryTextDelta",
        }:
            return str(params.get("delta") or "")
        if method == "error":
            return f"\n[error] {params.get('message') or params}\n"
        if self._is_user_response_request_message(message):
            approvals.append(self._approval_from_message(message))
            return "\n\n[Codex is waiting for a user response]\n"
        return ""
