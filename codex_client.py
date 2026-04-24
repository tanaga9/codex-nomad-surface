from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
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
    messages: list[dict[str, str]]
    before_offset: int | None = None
    has_older: bool = False


class ApprovalRequired(Exception):
    pass


OutputCallback = Callable[[str], None]


class CodexClient:
    # APPROVAL_POLICY = "untrusted"
    APPROVAL_POLICY = "on-request"
    WS_MAX_SIZE = 16 * 1024 * 1024

    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _connect_ws(self, websockets: Any) -> Any:
        return websockets.connect(
            self.base_url, open_timeout=self.timeout, max_size=self.WS_MAX_SIZE
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
        self, thread_id: str, limit: int = 40, before_offset: int | None = None
    ) -> CodexThreadMessages:
        if not thread_id or not self.base_url.startswith(("ws://", "wss://")):
            return CodexThreadMessages([])
        return asyncio.run(
            self._read_thread_messages_ws(thread_id, limit, before_offset)
        )

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
        self, thread_id: str, limit: int, before_offset: int | None
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
                raw = await self._rpc_call(
                    websocket, "thread/read", {"threadId": thread_id}, output, approvals
                )
        except Exception:
            return CodexThreadMessages([])

        path = self._thread_jsonl_path(raw)
        if path:
            result = self._read_thread_jsonl_messages(path, limit, before_offset)
            if result.messages:
                return result

        messages = self._parse_thread_messages(raw)
        if limit > 0:
            messages = messages[-limit:]
        return CodexThreadMessages(messages)

    def _approval_response_result(
        self, approval: dict[str, Any], decision: str
    ) -> dict[str, Any]:
        method = str(approval.get("method") or approval.get("title") or "")
        params = (
            approval.get("params") if isinstance(approval.get("params"), dict) else {}
        )
        approved = decision == "approve"
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            return {"decision": "accept" if approved else "decline"}
        if method == "item/permissions/requestApproval":
            if approved:
                permissions = (
                    params.get("permissions") if isinstance(params, dict) else {}
                )
                return {"permissions": permissions or {}, "scope": "turn"}
            return {"permissions": {}, "scope": "turn"}
        if method in {"execCommandApproval", "applyPatchApproval"}:
            return {"decision": "approved" if approved else "denied"}
        return {"decision": "accept" if approved else "decline"}

    async def _start_chat_turn_ws(
        self,
        project_path: str,
        prompt: str,
        thread_id: str | None,
        output_callback: OutputCallback | None = None,
    ) -> dict[str, Any]:
        try:
            import websockets
        except ModuleNotFoundError:
            return {
                "ok": False,
                "output": "`websockets` is not installed.",
            }

        output: list[str] = []
        approvals: list[dict[str, Any]] = []
        runtime: dict[str, Any] = {
            "output": output,
            "approvals": approvals,
            "thread_id": thread_id,
            "output_callback": output_callback,
        }
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
                    output,
                    approvals,
                    output_callback,
                )
                if thread_id:
                    thread_result = await self._rpc_call(
                        websocket,
                        "thread/resume",
                        {
                            "threadId": thread_id,
                            "cwd": project_path,
                            "approvalPolicy": self.APPROVAL_POLICY,
                            "persistExtendedHistory": True,
                        },
                        output,
                        approvals,
                        output_callback,
                        handle_approval_message,
                    )
                else:
                    thread_result = await self._rpc_call(
                        websocket,
                        "thread/start",
                        {
                            "cwd": project_path,
                            "approvalPolicy": self.APPROVAL_POLICY,
                            "ephemeral": False,
                            "sessionStartSource": "startup",
                            "experimentalRawEvents": False,
                            "persistExtendedHistory": True,
                        },
                        output,
                        approvals,
                        output_callback,
                        handle_approval_message,
                    )
                thread_id = thread_result["thread"]["id"]
                runtime["thread_id"] = thread_id
                await self._rpc_call(
                    websocket,
                    "turn/start",
                    {
                        "threadId": thread_id,
                        "cwd": project_path,
                        "approvalPolicy": self.APPROVAL_POLICY,
                        "input": [
                            {
                                "type": "text",
                                "text": prompt,
                                "text_elements": [],
                            }
                        ],
                    },
                    output,
                    approvals,
                    output_callback,
                    handle_approval_message,
                )
                return await self._collect_chat_turn_ws(runtime)
            except ApprovalRequired:
                return self._approval_result(runtime)
        except ApprovalRequired:
            return self._approval_result(runtime)
        except Exception as exc:
            await self._close_chat_turn_ws(runtime)
            text_output = "".join(output).strip()
            if text_output:
                text_output = f"{text_output}\n\n[send/receive error] {exc}"
            else:
                text_output = f"[send/receive error] {exc}"
            return {
                "ok": False,
                "thread_id": thread_id,
                "output": text_output,
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
            output = "".join(runtime.get("output") or []).strip()
            if output:
                output = f"{output}\n\n[send/receive error] {exc}"
            else:
                output = f"[send/receive error] {exc}"
            return {
                "ok": False,
                "thread_id": runtime.get("thread_id"),
                "output": output,
                "approvals": runtime.get("approvals") or [],
            }

    async def _collect_chat_turn_ws(self, runtime: dict[str, Any]) -> dict[str, Any]:
        websocket = runtime["websocket"]
        thread_id = runtime.get("thread_id")
        output = runtime["output"]
        approvals = runtime["approvals"]
        output_callback = runtime.get("output_callback")
        deadline = asyncio.get_running_loop().time() + 180.0
        while asyncio.get_running_loop().time() < deadline:
            remaining = max(0.1, deadline - asyncio.get_running_loop().time())
            try:
                raw_message = await asyncio.wait_for(
                    websocket.recv(), timeout=min(30.0, remaining)
                )
            except asyncio.TimeoutError:
                continue

            message = json.loads(raw_message)
            method = message.get("method")
            params = message.get("params") or {}
            if method and "requestApproval" in method:
                approval = self._approval_from_message(message)
                approvals.append(approval)
                runtime["approval"] = approval
                return self._approval_result(runtime)

            chunk = self._message_chunk(message, approvals)
            if chunk:
                output.append(chunk)
                if output_callback:
                    output_callback("".join(output))
            if method == "turn/completed" and params.get("threadId") == thread_id:
                await self._close_chat_turn_ws(runtime)
                return {
                    "ok": True,
                    "thread_id": thread_id,
                    "output": "".join(output).strip(),
                    "approvals": approvals,
                }
        await self._close_chat_turn_ws(runtime)
        return {
            "ok": False,
            "thread_id": thread_id,
            "output": "Codex turn did not complete within 180 seconds.",
            "approvals": approvals,
        }

    def _approval_result(self, runtime: dict[str, Any]) -> dict[str, Any]:
        runtime.pop("output_callback", None)
        return {
            "ok": False,
            "status": "approval",
            "thread_id": runtime.get("thread_id"),
            "output": "".join(runtime.get("output") or []).strip(),
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

    def _thread_jsonl_path(self, raw: Any) -> str:
        if not isinstance(raw, dict):
            return ""
        thread = raw.get("thread")
        if isinstance(thread, dict):
            return str(thread.get("path") or "")
        return str(raw.get("path") or "")

    def _read_thread_jsonl_messages(
        self, path: str, limit: int = 40, before_offset: int | None = None
    ) -> CodexThreadMessages:
        session_path = Path(path).expanduser()
        if not session_path.is_file():
            return CodexThreadMessages([])

        limit = max(1, limit)
        collected: list[tuple[int, dict[str, str]]] = []
        try:
            file_size = session_path.stat().st_size
            end_offset = (
                file_size
                if before_offset is None
                else max(0, min(before_offset, file_size))
            )
            position = end_offset
            pending = b""
            with session_path.open("rb") as handle:
                while position > 0 and len(collected) < limit:
                    chunk_size = min(65536, position)
                    position -= chunk_size
                    handle.seek(position)
                    block = handle.read(chunk_size) + pending
                    parts = block.split(b"\n")
                    if position > 0:
                        pending = parts[0]
                        complete = parts[1:]
                        line_offset = position + len(parts[0]) + 1
                    else:
                        pending = b""
                        complete = parts
                        line_offset = 0

                    lines: list[tuple[int, bytes]] = []
                    for line in complete:
                        lines.append((line_offset, line))
                        line_offset += len(line) + 1

                    for offset, line in reversed(lines):
                        message = self._thread_message_from_jsonl(line)
                        if message:
                            collected.append((offset, message))
                            if len(collected) >= limit:
                                break
        except OSError:
            return CodexThreadMessages([])

        if not collected:
            return CodexThreadMessages([])

        collected.sort(key=lambda item: item[0])
        earliest_offset = collected[0][0]
        return CodexThreadMessages(
            messages=[message for _, message in collected],
            before_offset=earliest_offset,
            has_older=earliest_offset > 0,
        )

    def _thread_message_from_jsonl(self, line: bytes) -> dict[str, str] | None:
        line = line.strip()
        if not line:
            return None
        try:
            record = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(record, dict):
            return None
        return self._thread_message_from_record(record)

    def _parse_thread_messages(self, raw: Any) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []

        def collect(value: Any) -> None:
            if isinstance(value, list):
                for item in value:
                    collect(item)
                return
            if not isinstance(value, dict):
                return

            message = self._thread_message_from_record(value)
            if message:
                messages.append(message)
                return

            for key in ("turns", "items", "messages"):
                nested = value.get(key)
                if nested:
                    collect(nested)

        collect(raw)
        return messages

    def _thread_message_from_record(
        self, record: dict[str, Any]
    ) -> dict[str, str] | None:
        payload = (
            record.get("payload") if record.get("type") == "response_item" else record
        )
        if not isinstance(payload, dict) or payload.get("type") != "message":
            return None

        role = str(payload.get("role") or "")
        if role not in {"user", "assistant"}:
            return None

        content = self._content_text(payload.get("content")).strip()
        if not content:
            content = str(payload.get("message") or "").strip()
        if not content or (role == "user" and self._is_internal_user_message(content)):
            return None
        return {"role": role, "content": content}

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

    def _is_internal_user_message(self, content: str) -> bool:
        stripped = content.lstrip()
        return stripped.startswith(
            (
                "<environment_context>",
                "<permissions instructions>",
                "<app-context>",
                "<collaboration_mode>",
                "<apps_instructions>",
                "<skills_instructions>",
                "<plugins_instructions>",
            )
        )

    def _approval_from_message(self, message: dict[str, Any]) -> dict[str, Any]:
        method = str(message.get("method") or "")
        params = message.get("params") or {}
        return {
            "id": message.get("id"),
            "method": method,
            "params": params,
            "title": method,
            "detail": json.dumps(params, ensure_ascii=False, indent=2),
        }

    async def _rpc_call(
        self,
        websocket: Any,
        method: str,
        params: dict[str, Any],
        output: list[str],
        approvals: list[dict[str, Any]],
        output_callback: OutputCallback | None = None,
        approval_handler: Any | None = None,
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
                and message.get("method")
                and "requestApproval" in str(message.get("method"))
            ):
                await approval_handler(message)
                continue
            chunk = self._handle_ws_message(message, output, approvals)
            if chunk and output_callback:
                output_callback("".join(output))
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
        elif method and "requestApproval" in method:
            approvals.append(self._approval_from_message(message))
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
        if method and "requestApproval" in method:
            approvals.append(self._approval_from_message(message))
            return "\n\n[An operation requires approval]\n"
        return ""
