import asyncio
import json
import threading
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


def test_failed_revision_write_leaves_previous_commit_visible(
    isolated_text_root, monkeypatch
):
    manifest = text_store.initialize_text_draft("revision-failure", "/path/to/project")
    text_id = manifest["text_id"]
    text_store.save_text(text_id, "committed", expected_revision=0)
    original_atomic_write = text_store._atomic_write

    def fail_revision(path, data):
        if path.name == "00000002.md":
            raise OSError("revision write failed")
        original_atomic_write(path, data)

    monkeypatch.setattr(text_store, "_atomic_write", fail_revision)
    with pytest.raises(OSError, match="revision write failed"):
        text_store.save_text(text_id, "not committed", expected_revision=1)
    monkeypatch.setattr(text_store, "_atomic_write", original_atomic_write)

    stored_manifest, content = text_store.load_text_snapshot(text_id)
    assert stored_manifest["current_revision"] == 1
    assert content == "committed"


def test_failed_manifest_write_ignores_uncommitted_revision(
    isolated_text_root, monkeypatch
):
    manifest = text_store.initialize_text_draft("manifest-failure", "/path/to/project")
    text_id = manifest["text_id"]
    text_store.save_text(text_id, "committed", expected_revision=0)
    original_atomic_write = text_store._atomic_write

    def fail_manifest(path, data):
        if path.name == "manifest.json":
            raise OSError("manifest write failed")
        original_atomic_write(path, data)

    monkeypatch.setattr(text_store, "_atomic_write", fail_manifest)
    with pytest.raises(OSError, match="manifest write failed"):
        text_store.save_text(text_id, "orphaned", expected_revision=1)
    monkeypatch.setattr(text_store, "_atomic_write", original_atomic_write)

    stored_manifest, content = text_store.load_text_snapshot(text_id)
    assert stored_manifest["current_revision"] == 1
    assert content == "committed"
    assert (
        isolated_text_root / text_id / "revisions" / "00000002.md"
    ).read_text(encoding="utf-8") == "orphaned"

    retried = text_store.save_text(text_id, "retried", expected_revision=1)
    assert retried["current_revision"] == 2
    assert text_store.load_text(text_id) == "retried"


def test_current_projection_failure_does_not_undo_committed_revision(
    isolated_text_root, monkeypatch
):
    manifest = text_store.initialize_text_draft(
        "projection-failure", "/path/to/project"
    )
    text_id = manifest["text_id"]
    text_store.save_text(text_id, "before", expected_revision=0)
    original_atomic_write = text_store._atomic_write

    def fail_current(path, data):
        if path.name == "current.md":
            raise OSError("projection write failed")
        original_atomic_write(path, data)

    monkeypatch.setattr(text_store, "_atomic_write", fail_current)
    committed = text_store.save_text(text_id, "after", expected_revision=1)
    stored_manifest, content = text_store.load_text_snapshot(text_id)
    assert committed["current_revision"] == 2
    assert stored_manifest["current_revision"] == 2
    assert content == "after"

    monkeypatch.setattr(text_store, "_atomic_write", original_atomic_write)
    assert text_store.load_text(text_id) == "after"
    assert (isolated_text_root / text_id / "current.md").read_text(
        encoding="utf-8"
    ) == "after"


def test_legacy_revision_zero_is_promoted_to_immutable_revision(isolated_text_root):
    manifest = text_store.initialize_text_draft("legacy-zero", "/path/to/project")
    text_id = manifest["text_id"]
    directory = isolated_text_root / text_id
    (directory / "revisions" / "00000000.md").unlink()
    (directory / "current.md").write_text("legacy content", encoding="utf-8")
    manifest_path = directory / "manifest.json"
    stored_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    stored_manifest["schema_version"] = 1
    stored_manifest.pop("content_sha256")
    manifest_path.write_text(json.dumps(stored_manifest), encoding="utf-8")

    loaded_manifest, content = text_store.load_text_snapshot(text_id)

    assert loaded_manifest["current_revision"] == 0
    assert content == "legacy content"
    assert (directory / "revisions" / "00000000.md").read_text(
        encoding="utf-8"
    ) == "legacy content"


def test_corrupt_revision_is_repaired_from_hash_verified_current(isolated_text_root):
    manifest = text_store.initialize_text_draft("corrupt-revision", "/path/to/project")
    text_id = manifest["text_id"]
    text_store.save_text(text_id, "committed", expected_revision=0)
    directory = isolated_text_root / text_id
    revision_path = directory / "revisions" / "00000001.md"
    revision_path.write_text("corrupt", encoding="utf-8")

    stored_manifest, content = text_store.load_text_snapshot(text_id)

    assert stored_manifest["current_revision"] == 1
    assert content == "committed"
    assert revision_path.read_text(encoding="utf-8") == "committed"


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
    assert payload["text"] == "# Heading"
    assert payload["scope"]["headings"] == [
        {
            "text": "Heading",
            "level": 1,
            "line": 1,
            "marker_line": 1,
            "style": "atx",
        }
    ]
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


def test_text_broker_disconnect_fails_pending_request_immediately():
    async def scenario() -> None:
        broker = text_runtime.TextBroker()
        connection = broker.register("text-disconnect")
        assert broker.activate("text-disconnect", connection)
        broker_call = asyncio.create_task(
            asyncio.to_thread(broker.call, "text-disconnect", "sync_snapshot")
        )
        request = await connection.outgoing.get()
        assert request["request"]["method"] == "sync_snapshot"

        broker.unregister("text-disconnect", connection)

        with pytest.raises(RuntimeError, match="text_disconnected"):
            await asyncio.wait_for(broker_call, timeout=1)
        assert not broker.has_pending(request["request"]["id"])

    asyncio.run(scenario())


def test_text_broker_replacement_fails_only_previous_connection_requests():
    async def scenario() -> None:
        broker = text_runtime.TextBroker()
        previous = broker.register("text-replaced")
        assert broker.activate("text-replaced", previous)
        previous_call = asyncio.create_task(
            asyncio.to_thread(broker.call, "text-replaced", "read", {"scope": "all"})
        )
        previous_request = await previous.outgoing.get()

        current = broker.register("text-replaced")

        with pytest.raises(RuntimeError, match="text_replaced"):
            await asyncio.wait_for(previous_call, timeout=1)
        assert previous.retired.is_set()
        assert not broker.has_pending(previous_request["request"]["id"])
        assert broker.is_ready(current)
        assert broker.activate("text-replaced", current)

        current_call = asyncio.create_task(
            asyncio.to_thread(broker.call, "text-replaced", "read", {"scope": "all"})
        )
        current_request = await current.outgoing.get()
        broker.resolve(
            current_request["request"]["id"],
            {"ok": True, "payload": {"live": True}},
        )
        assert await asyncio.wait_for(current_call, timeout=1) == {
            "ok": True,
            "payload": {"live": True},
        }

    asyncio.run(scenario())


def test_text_broker_replacement_allows_claimed_response_to_finish():
    async def scenario() -> None:
        broker = text_runtime.TextBroker()
        previous = broker.register("text-processing")
        assert broker.activate("text-processing", previous)
        previous_call = asyncio.create_task(
            asyncio.to_thread(broker.call, "text-processing", "apply_patch")
        )
        request = await previous.outgoing.get()
        request_id = request["request"]["id"]

        assert broker.claim_response(request_id, previous) == ("apply_patch", {})
        current = broker.register("text-processing")

        await asyncio.sleep(0)
        assert not previous_call.done()
        assert not broker.is_ready(current)
        assert previous.retired.is_set()
        assert broker.claim_response(request_id, current) is None

        broker.resolve(request_id, {"ok": True, "payload": {"revision": 1}})
        assert await asyncio.wait_for(previous_call, timeout=1) == {
            "ok": True,
            "payload": {"revision": 1},
        }
        assert broker.is_ready(current)
        assert broker.activate("text-processing", current)
        assert not broker.has_pending(request_id)

    asyncio.run(scenario())


def test_text_session_is_released_after_waiting_connection_disappears():
    async def scenario() -> None:
        broker = text_runtime.TextBroker()
        previous = broker.register("text-session-cleanup")
        assert broker.activate("text-session-cleanup", previous)
        operation = broker.begin_operation("text-session-cleanup", previous)
        assert operation is not None

        current = broker.register("text-session-cleanup")
        broker.unregister("text-session-cleanup", current)
        assert "text-session-cleanup" in broker._sessions

        broker.finish_operation(operation)
        assert "text-session-cleanup" not in broker._sessions

    asyncio.run(scenario())


def test_text_broker_timeout_does_not_abandon_claimed_response(monkeypatch):
    async def scenario() -> None:
        monkeypatch.setattr(text_runtime, "TEXT_TOOL_TIMEOUT_SECONDS", 0.01)
        broker = text_runtime.TextBroker()
        connection = broker.register("text-slow-processing")
        assert broker.activate("text-slow-processing", connection)
        broker_call = asyncio.create_task(
            asyncio.to_thread(broker.call, "text-slow-processing", "apply_patch")
        )
        request = await connection.outgoing.get()
        request_id = request["request"]["id"]
        assert broker.claim_response(request_id, connection) == ("apply_patch", {})

        await asyncio.sleep(0.03)
        assert not broker_call.done()

        broker.resolve(request_id, {"ok": True, "payload": {"revision": 1}})
        assert await asyncio.wait_for(broker_call, timeout=1) == {
            "ok": True,
            "payload": {"revision": 1},
        }

    asyncio.run(scenario())


def test_blocking_mutation_finishes_before_cancellation_propagates():
    async def scenario() -> None:
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def mutation() -> None:
            started.set()
            release.wait(timeout=1)
            finished.set()

        mutation_task = asyncio.create_task(
            text_runtime._run_blocking_mutation(mutation)
        )
        assert await asyncio.to_thread(started.wait, 1)
        mutation_task.cancel()
        await asyncio.sleep(0)
        assert not mutation_task.done()

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(mutation_task, timeout=1)
        assert finished.is_set()

    asyncio.run(scenario())


def test_websocket_disconnect_releases_pending_broker_call(
    isolated_text_root, monkeypatch
):
    async def scenario() -> None:
        manifest = text_store.initialize_text("thread-disconnect-pending")
        text_id = manifest["text_id"]
        broker = text_runtime.TextBroker()
        monkeypatch.setattr(text_runtime, "TEXT_BROKER", broker)
        monkeypatch.setattr(text_runtime, "auth_required", lambda: False)
        websocket = _FakeTextWebSocket(text_id)
        websocket_task = asyncio.create_task(text_runtime.text_websocket(websocket))
        await _wait_for_message(websocket, "sync")

        broker_call = asyncio.create_task(
            asyncio.to_thread(broker.call, text_id, "sync_snapshot")
        )
        await _wait_for_message(websocket, "request")
        await websocket.incoming.put(WebSocketDisconnect())
        await asyncio.wait_for(websocket_task, timeout=1)

        with pytest.raises(RuntimeError, match="text_disconnected"):
            await asyncio.wait_for(broker_call, timeout=1)

    asyncio.run(scenario())


def test_replacement_sync_waits_for_checkpoint_to_commit(
    isolated_text_root, monkeypatch
):
    async def scenario() -> None:
        manifest = text_store.initialize_text("thread-checkpoint-replacement")
        text_id = manifest["text_id"]
        broker = text_runtime.TextBroker()
        monkeypatch.setattr(text_runtime, "TEXT_BROKER", broker)
        monkeypatch.setattr(text_runtime, "auth_required", lambda: False)

        save_started = threading.Event()
        allow_save = threading.Event()
        original_save = text_runtime.save_text

        def delayed_save(*args, **kwargs):
            save_started.set()
            if not allow_save.wait(timeout=1):
                raise TimeoutError("test checkpoint save was not released")
            return original_save(*args, **kwargs)

        monkeypatch.setattr(text_runtime, "save_text", delayed_save)
        previous_websocket = _FakeTextWebSocket(text_id)
        previous_task = asyncio.create_task(
            text_runtime.text_websocket(previous_websocket)
        )
        await _wait_for_message(previous_websocket, "sync")
        await previous_websocket.incoming.put(
            {
                "type": "checkpoint",
                "content": "checkpoint committed before replacement sync",
                "base_revision": 0,
            }
        )
        assert await asyncio.to_thread(save_started.wait, 1)

        current_websocket = _FakeTextWebSocket(text_id)
        current_task = asyncio.create_task(
            text_runtime.text_websocket(current_websocket)
        )
        for _ in range(100):
            if previous_websocket.closed == text_runtime.TEXT_REPLACED_CLOSE_CODE:
                break
            await asyncio.sleep(0.01)
        assert previous_websocket.closed == text_runtime.TEXT_REPLACED_CLOSE_CODE
        assert not any(
            message.get("type") == "sync" for message in current_websocket.sent
        )

        allow_save.set()
        sync = await _wait_for_message(current_websocket, "sync")
        assert sync["content"] == "checkpoint committed before replacement sync"
        assert sync["revision"] == 1

        await previous_websocket.incoming.put(WebSocketDisconnect())
        await current_websocket.incoming.put(WebSocketDisconnect())
        await asyncio.wait_for(previous_task, timeout=1)
        await asyncio.wait_for(current_task, timeout=1)

    asyncio.run(scenario())


def test_replacement_sync_waits_for_claimed_response_to_commit(
    isolated_text_root, monkeypatch
):
    async def scenario() -> None:
        manifest = text_store.initialize_text("thread-replacement-sync")
        text_id = manifest["text_id"]
        broker = text_runtime.TextBroker()
        monkeypatch.setattr(text_runtime, "TEXT_BROKER", broker)
        monkeypatch.setattr(text_runtime, "auth_required", lambda: False)

        save_started = threading.Event()
        allow_save = threading.Event()
        original_save = text_runtime.save_text

        def delayed_save(*args, **kwargs):
            save_started.set()
            if not allow_save.wait(timeout=1):
                raise TimeoutError("test save was not released")
            return original_save(*args, **kwargs)

        monkeypatch.setattr(text_runtime, "save_text", delayed_save)
        previous_websocket = _FakeTextWebSocket(text_id)
        previous_task = asyncio.create_task(
            text_runtime.text_websocket(previous_websocket)
        )
        await _wait_for_message(previous_websocket, "sync")

        broker_call = asyncio.create_task(
            asyncio.to_thread(
                broker.call,
                text_id,
                "apply_patch",
                {"base_revision": 0, "operations": []},
            )
        )
        request = await _wait_for_message(previous_websocket, "request")
        await previous_websocket.incoming.put(
            {
                "type": "response",
                "id": request["request"]["id"],
                "ok": True,
                "payload": {
                    "content": "committed before replacement sync",
                    "base_revision": 0,
                    "changed_operations": 1,
                },
            }
        )
        assert await asyncio.to_thread(save_started.wait, 1)

        current_websocket = _FakeTextWebSocket(text_id)
        current_task = asyncio.create_task(
            text_runtime.text_websocket(current_websocket)
        )
        for _ in range(100):
            if previous_websocket.closed == text_runtime.TEXT_REPLACED_CLOSE_CODE:
                break
            await asyncio.sleep(0.01)
        assert previous_websocket.closed == text_runtime.TEXT_REPLACED_CLOSE_CODE
        assert not any(
            message.get("type") == "sync" for message in current_websocket.sent
        )

        newest_websocket = _FakeTextWebSocket(text_id)
        newest_task = asyncio.create_task(
            text_runtime.text_websocket(newest_websocket)
        )
        for _ in range(100):
            if current_websocket.closed == text_runtime.TEXT_REPLACED_CLOSE_CODE:
                break
            await asyncio.sleep(0.01)
        assert current_websocket.closed == text_runtime.TEXT_REPLACED_CLOSE_CODE
        await asyncio.wait_for(current_task, timeout=1)
        assert not any(
            message.get("type") == "sync" for message in newest_websocket.sent
        )

        allow_save.set()
        result = await asyncio.wait_for(broker_call, timeout=1)
        assert result["ok"] is True
        sync = await _wait_for_message(newest_websocket, "sync")
        assert sync["content"] == "committed before replacement sync"
        assert sync["revision"] == 1

        await previous_websocket.incoming.put(WebSocketDisconnect())
        await newest_websocket.incoming.put(WebSocketDisconnect())
        await asyncio.wait_for(previous_task, timeout=1)
        await asyncio.wait_for(newest_task, timeout=1)

    asyncio.run(scenario())


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


def test_live_read_scopes_browser_snapshot_on_server(isolated_text_root, monkeypatch):
    async def scenario() -> None:
        manifest = text_store.initialize_text(
            "thread-live-read", editor_kind="document"
        )
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
                "read",
                {"scope": "section", "heading": "Live"},
            )
        )
        request = await _wait_for_message(websocket, "request")
        await websocket.incoming.put(
            {
                "type": "response",
                "id": request["request"]["id"],
                "ok": True,
                "payload": {
                    "content": "# Live\nunsaved\n# Next\nbody",
                    "revision": 0,
                    "selection": {
                        "selection_kind": "caret",
                        "anchor": {"line": 2, "column": 1},
                        "head": {"line": 2, "column": 1},
                        "from_line": 2,
                        "to_line": 2,
                        "text": "",
                    },
                },
            }
        )

        result = await asyncio.wait_for(broker_call, timeout=2)
        assert result["ok"] is True
        assert result["payload"]["text"] == "# Live\nunsaved\n"
        assert result["payload"]["scope"]["to_line"] == 2
        assert result["payload"]["live"] is True

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


def test_sync_snapshot_persists_current_browser_text_before_agent_send(
    isolated_text_root, monkeypatch
):
    async def scenario() -> None:
        manifest = text_store.initialize_text("thread-sync-snapshot")
        text_id = manifest["text_id"]
        broker = text_runtime.TextBroker()
        monkeypatch.setattr(text_runtime, "TEXT_BROKER", broker)
        monkeypatch.setattr(text_runtime, "auth_required", lambda: False)
        websocket = _FakeTextWebSocket(text_id)
        websocket_task = asyncio.create_task(text_runtime.text_websocket(websocket))
        await _wait_for_message(websocket, "sync")

        broker_call = asyncio.create_task(
            asyncio.to_thread(text_runtime.sync_text_for_agent, text_id)
        )
        request = await _wait_for_message(websocket, "request")
        assert request["request"]["method"] == "sync_snapshot"
        await websocket.incoming.put(
            {
                "type": "response",
                "id": request["request"]["id"],
                "ok": True,
                "payload": {"text": "latest before send", "revision": 0},
            }
        )

        result = await asyncio.wait_for(broker_call, timeout=2)
        commit = await _wait_for_message(websocket, "snapshot_commit")
        assert result == {"revision": 1}
        assert commit["content"] == "latest before send"
        assert text_store.load_text(text_id) == "latest before send"

        await websocket.incoming.put(WebSocketDisconnect())
        await asyncio.wait_for(websocket_task, timeout=2)

    asyncio.run(scenario())
