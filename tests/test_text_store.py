import asyncio
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from starlette.websockets import WebSocketDisconnect

from codex_nomad_surface import text_runtime, text_store
from codex_nomad_surface.text_runtime import (
    TEXT_DEVELOPER_INSTRUCTIONS,
    text_dynamic_tool_handler_for_text,
    text_dynamic_tools,
    text_initial_context_items,
)


@pytest.fixture
def isolated_text_root(tmp_path, monkeypatch):
    monkeypatch.setattr(text_store, "TEXT_ROOT", tmp_path / "texts")
    return tmp_path / "texts"


def test_text_is_file_backed_revisioned_and_bounded(isolated_text_root, monkeypatch):
    monkeypatch.setattr(text_store, "TEXT_REVISION_LIMIT", 2)
    manifest = text_store.initialize_text(
        "thread-text-store",
        "/path/to/project",
        text_format="markdown",
        presentation="assisted",
    )
    text_id = manifest["text_id"]

    first = text_store.save_text(text_id, "# First\n", expected_revision=0)
    second = text_store.save_text(text_id, "# Second\n", expected_revision=1)
    third = text_store.save_text(text_id, "# Third\n", expected_revision=2)

    assert first["current_revision"] == 1
    assert second["current_revision"] == 2
    assert third["current_revision"] == 3
    assert text_store.load_text(text_id) == "# Third\n"
    assert [path.name for path in sorted((isolated_text_root / text_id / "revisions").iterdir())] == [
        "00000002.md",
        "00000003.md",
    ]
    stored_manifest = json.loads(
        (isolated_text_root / text_id / "manifest.json").read_text(encoding="utf-8")
    )
    assert stored_manifest["format"] == "markdown"
    assert stored_manifest["presentation"] == "assisted"
    assert stored_manifest["editor_kind"] == "document"


def test_text_save_rejects_stale_revision(isolated_text_root):
    manifest = text_store.initialize_text_draft("draft-one", "/path/to/project")
    text_id = manifest["text_id"]
    text_store.save_text(text_id, "human edit", expected_revision=0)

    with pytest.raises(text_store.TextRevisionConflict) as conflict:
        text_store.save_text(text_id, "stale AI edit", expected_revision=0)

    assert conflict.value.current_revision == 1
    assert text_store.load_text(text_id) == "human edit"


def test_plain_text_forces_raw_presentation_and_exports_txt(isolated_text_root):
    manifest = text_store.initialize_text_draft(
        "plain-draft",
        "/path/to/project",
        text_format="plain",
        presentation="assisted",
    )
    text_id = manifest["text_id"]
    assert manifest["presentation"] == "raw"
    assert manifest["editor_kind"] == "text"
    text_store.save_text(text_id, "memo", expected_revision=0)

    exported = text_store.save_text_export(text_id)

    assert exported["format"] == "txt"
    assert Path(exported["export_path"]).read_text(encoding="utf-8") == "memo"


def test_editor_kind_controls_storage_format(isolated_text_root):
    text_manifest = text_store.initialize_text_draft(
        "text-editor",
        "/path/to/project",
        editor_kind="text",
        text_format="markdown",
        presentation="assisted",
    )
    document_manifest = text_store.initialize_text_draft(
        "document-editor",
        "/path/to/project",
        editor_kind="document",
        text_format="plain",
        presentation="raw",
    )

    assert (text_manifest["editor_kind"], text_manifest["format"]) == (
        "text",
        "plain",
    )
    assert text_manifest["presentation"] == "raw"
    assert (document_manifest["editor_kind"], document_manifest["format"]) == (
        "document",
        "markdown",
    )


def test_legacy_manifest_infers_editor_kind_from_format(isolated_text_root):
    manifest = text_store.initialize_text_draft(
        "legacy-document",
        "/path/to/project",
        text_format="markdown",
    )
    manifest_path = isolated_text_root / manifest["text_id"] / "manifest.json"
    stored = json.loads(manifest_path.read_text(encoding="utf-8"))
    stored.pop("editor_kind")
    manifest_path.write_text(json.dumps(stored), encoding="utf-8")

    loaded = text_store.read_text_manifest(manifest["text_id"])

    assert loaded["editor_kind"] == "document"


def test_text_binding_survives_draft_to_thread_transition(isolated_text_root):
    manifest = text_store.initialize_text_draft("draft-bind", "/path/to/project")
    bound = text_store.bind_text_to_thread(manifest["text_id"], "thread-bound")

    assert bound["thread_id"] == "thread-bound"
    assert text_store.text_manifest_for_thread("thread-bound")["text_id"] == manifest["text_id"]


def test_text_dynamic_tool_schemas_are_valid_and_offline_read_works(isolated_text_root):
    manifest = text_store.initialize_text("thread-tools", text_format="markdown")
    text_id = manifest["text_id"]
    text_store.save_text(text_id, "# Heading\n\nBody\n", expected_revision=0)

    namespaces = text_dynamic_tools()
    assert len(namespaces) == 1
    assert namespaces[0]["type"] == "namespace"
    assert namespaces[0]["name"] == "text"
    assert [tool["type"] for tool in namespaces[0]["tools"]] == [
        "function",
        "function",
        "function",
    ]
    for namespace in namespaces:
        for tool in namespace["tools"]:
            Draft202012Validator.check_schema(tool["inputSchema"])

    apply_schema = next(
        tool["inputSchema"]
        for tool in namespaces[0]["tools"]
        if tool["name"] == "apply_patch"
    )
    validator = Draft202012Validator(apply_schema)
    assert list(
        validator.iter_errors(
            {
                "base_revision": 0,
                "operations": [
                    {"type": "replace", "old_text": "before", "new_text": "after"}
                ],
            }
        )
    )
    assert not list(
        validator.iter_errors(
            {
                "base_revision": 0,
                "operations": [
                    {
                        "type": "replace_document",
                        "old_text": "before",
                        "new_text": "after",
                    }
                ],
            }
        )
    )

    result = text_dynamic_tool_handler_for_text(text_id)(
        {"namespace": "text", "tool": "read", "arguments": {"scope": "outline"}}
    )
    payload = json.loads(result["contentItems"][0]["text"])

    assert result["success"] is True
    assert payload["text"] == "# Heading\n"
    assert payload["revision"] == 1
    assert TEXT_DEVELOPER_INSTRUCTIONS.startswith("This thread uses")
    assert text_initial_context_items() == [
        {
            "type": "message",
            "role": "developer",
            "content": [
                {
                    "type": "input_text",
                    "text": TEXT_DEVELOPER_INSTRUCTIONS,
                }
            ],
        }
    ]


class _FakeTextWebSocket:
    def __init__(self, text_id: str) -> None:
        self.path_params = {"text_id": text_id}
        self.scope: dict = {}
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.accepted = False
        self.closed: int | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int) -> None:
        self.closed = code

    async def send_json(self, message: dict) -> None:
        self.sent.append(message)

    async def receive_json(self) -> dict:
        message = await self.incoming.get()
        if isinstance(message, BaseException):
            raise message
        return message


async def _wait_for_message(websocket: _FakeTextWebSocket, message_type: str) -> dict:
    for _ in range(100):
        message = next(
            (item for item in websocket.sent if item.get("type") == message_type),
            None,
        )
        if message:
            return message
        await asyncio.sleep(0.01)
    raise AssertionError(f"Timed out waiting for {message_type}.")


def test_ai_patch_is_saved_before_commit_is_sent(isolated_text_root, monkeypatch):
    async def scenario() -> None:
        manifest = text_store.initialize_text("thread-two-phase")
        text_id = manifest["text_id"]
        text_store.save_text(text_id, "before", expected_revision=0)
        broker = text_runtime.TextBroker()
        monkeypatch.setattr(text_runtime, "TEXT_BROKER", broker)
        monkeypatch.setattr(text_runtime, "auth_required", lambda: False)
        websocket = _FakeTextWebSocket(text_id)
        websocket_task = asyncio.create_task(text_runtime.text_websocket(websocket))
        await _wait_for_message(websocket, "sync")

        broker_call = asyncio.create_task(
            asyncio.to_thread(
                broker.call,
                text_id,
                "apply_patch",
                {"base_revision": 1, "operations": []},
            )
        )
        request = await _wait_for_message(websocket, "request")
        assert text_store.load_text(text_id) == "before"
        await websocket.incoming.put(
            {
                "type": "response",
                "id": request["request"]["id"],
                "ok": True,
                "payload": {
                    "content": "after",
                    "base_revision": 1,
                    "changed_operations": 1,
                },
            }
        )

        result = await asyncio.wait_for(broker_call, timeout=2)
        commit = await _wait_for_message(websocket, "commit")
        assert result["ok"] is True
        assert text_store.load_text(text_id) == "after"
        assert commit["revision"] == 2
        assert commit["content"] == "after"

        await websocket.incoming.put(WebSocketDisconnect())
        await asyncio.wait_for(websocket_task, timeout=2)

    asyncio.run(scenario())


def test_ai_patch_save_conflict_sends_reject_without_overwrite(
    isolated_text_root, monkeypatch
):
    async def scenario() -> None:
        manifest = text_store.initialize_text("thread-two-phase-conflict")
        text_id = manifest["text_id"]
        broker = text_runtime.TextBroker()
        monkeypatch.setattr(text_runtime, "TEXT_BROKER", broker)
        monkeypatch.setattr(text_runtime, "auth_required", lambda: False)
        websocket = _FakeTextWebSocket(text_id)
        websocket_task = asyncio.create_task(text_runtime.text_websocket(websocket))
        await _wait_for_message(websocket, "sync")

        broker_call = asyncio.create_task(
            asyncio.to_thread(
                broker.call,
                text_id,
                "apply_patch",
                {"base_revision": 0, "operations": []},
            )
        )
        request = await _wait_for_message(websocket, "request")
        text_store.save_text(text_id, "human change", expected_revision=0)
        await websocket.incoming.put(
            {
                "type": "response",
                "id": request["request"]["id"],
                "ok": True,
                "payload": {
                    "content": "AI change",
                    "base_revision": 0,
                    "changed_operations": 1,
                },
            }
        )

        result = await asyncio.wait_for(broker_call, timeout=2)
        reject = await _wait_for_message(websocket, "reject")
        assert result["ok"] is False
        assert result["error"] == "revision_conflict"
        assert text_store.load_text(text_id) == "human change"
        assert reject["revision"] == 1
        assert reject["content"] == "human change"

        await websocket.incoming.put(WebSocketDisconnect())
        await asyncio.wait_for(websocket_task, timeout=2)

    asyncio.run(scenario())


def test_checkpoint_conflict_returns_canonical_content(isolated_text_root, monkeypatch):
    async def scenario() -> None:
        manifest = text_store.initialize_text("thread-checkpoint-conflict")
        text_id = manifest["text_id"]
        text_store.save_text(text_id, "saved", expected_revision=0)
        broker = text_runtime.TextBroker()
        monkeypatch.setattr(text_runtime, "TEXT_BROKER", broker)
        monkeypatch.setattr(text_runtime, "auth_required", lambda: False)
        websocket = _FakeTextWebSocket(text_id)
        websocket_task = asyncio.create_task(text_runtime.text_websocket(websocket))
        await _wait_for_message(websocket, "sync")

        await websocket.incoming.put(
            {
                "type": "checkpoint",
                "base_revision": 0,
                "content": "local",
            }
        )
        acknowledgement = await _wait_for_message(websocket, "checkpoint_ack")
        assert acknowledgement == {
            "type": "checkpoint_ack",
            "ok": False,
            "error": "revision_conflict",
            "revision": 1,
            "content": "saved",
        }
        assert text_store.load_text(text_id) == "saved"

        await websocket.incoming.put(WebSocketDisconnect())
        await asyncio.wait_for(websocket_task, timeout=2)

    asyncio.run(scenario())


def test_export_snapshot_updates_browser_revision_after_save(
    isolated_text_root, monkeypatch
):
    async def scenario() -> None:
        manifest = text_store.initialize_text("thread-export-snapshot")
        text_id = manifest["text_id"]
        broker = text_runtime.TextBroker()
        monkeypatch.setattr(text_runtime, "TEXT_BROKER", broker)
        monkeypatch.setattr(text_runtime, "auth_required", lambda: False)
        websocket = _FakeTextWebSocket(text_id)
        websocket_task = asyncio.create_task(text_runtime.text_websocket(websocket))
        await _wait_for_message(websocket, "sync")

        broker_call = asyncio.create_task(
            asyncio.to_thread(broker.call, text_id, "export_snapshot")
        )
        request = await _wait_for_message(websocket, "request")
        await websocket.incoming.put(
            {
                "type": "response",
                "id": request["request"]["id"],
                "ok": True,
                "payload": {"text": "latest", "revision": 0},
            }
        )

        result = await asyncio.wait_for(broker_call, timeout=2)
        commit = await _wait_for_message(websocket, "snapshot_commit")
        assert result["ok"] is True
        assert result["payload"] == {"revision": 1}
        assert commit["revision"] == 1
        assert commit["content"] == "latest"
        assert text_store.load_text(text_id) == "latest"

        await websocket.incoming.put(WebSocketDisconnect())
        await asyncio.wait_for(websocket_task, timeout=2)

    asyncio.run(scenario())


def test_export_snapshot_uses_canonical_revision_when_content_is_already_saved(
    isolated_text_root, monkeypatch
):
    async def scenario() -> None:
        manifest = text_store.initialize_text("thread-export-existing")
        text_id = manifest["text_id"]
        text_store.save_text(text_id, "already saved", expected_revision=0)
        broker = text_runtime.TextBroker()
        monkeypatch.setattr(text_runtime, "TEXT_BROKER", broker)
        monkeypatch.setattr(text_runtime, "auth_required", lambda: False)
        websocket = _FakeTextWebSocket(text_id)
        websocket_task = asyncio.create_task(text_runtime.text_websocket(websocket))
        await _wait_for_message(websocket, "sync")

        broker_call = asyncio.create_task(
            asyncio.to_thread(broker.call, text_id, "export_snapshot")
        )
        request = await _wait_for_message(websocket, "request")
        await websocket.incoming.put(
            {
                "type": "response",
                "id": request["request"]["id"],
                "ok": True,
                "payload": {"text": "already saved", "revision": 0},
            }
        )

        result = await asyncio.wait_for(broker_call, timeout=2)
        commit = await _wait_for_message(websocket, "snapshot_commit")
        assert result["payload"] == {"revision": 1}
        assert commit["revision"] == 1
        assert commit["content"] == "already saved"
        assert text_store.read_text_manifest(text_id)["current_revision"] == 1

        await websocket.incoming.put(WebSocketDisconnect())
        await asyncio.wait_for(websocket_task, timeout=2)

    asyncio.run(scenario())
