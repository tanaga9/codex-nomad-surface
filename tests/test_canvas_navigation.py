import asyncio
from unittest.mock import Mock

import pytest

from codex_nomad_surface import app, canvas_runtime
from codex_nomad_surface.chat_store import ChatSession
from codex_nomad_surface.settings import Project


class State(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


@pytest.fixture
def navigation(monkeypatch):
    project = Project(name="Project", path="/path/to/project")
    chat = ChatSession.new(project.path)
    chat.canvas_id = "canvas-source"
    chat.surface = "canvas"
    state = State(canvas_navigation_owner="session-a", canvas_navigation_source=(project, chat), selected_chat_id="other")
    monkeypatch.setattr(app.st, "session_state", state)
    monkeypatch.setattr(app.st, "query_params", {})
    return state, project, chat


def test_navigation_saves_source_canvas_before_releasing_it(navigation, monkeypatch):
    state, _, chat = navigation
    save = Mock()
    monkeypatch.setattr(app, "prepare_canvas_leave", save)
    assert app.save_canvas_before_navigation(None)
    save.assert_called_once_with(chat.canvas_id, "session-a")
    assert "canvas_navigation_source" not in state


def test_rerendering_same_canvas_does_not_pause_editing(navigation, monkeypatch):
    _, _, chat = navigation
    save = Mock()
    monkeypatch.setattr(app, "prepare_canvas_leave", save)
    assert app.save_canvas_before_navigation(chat)
    save.assert_not_called()


def test_failed_navigation_restores_canvas_selection(navigation, monkeypatch):
    state, project, chat = navigation
    monkeypatch.setattr(app, "prepare_canvas_leave", Mock(side_effect=RuntimeError("save failed")))
    assert not app.save_canvas_before_navigation(None)
    assert state.selected_chat_id == chat.id
    assert state[app.PENDING_CHAT_SELECT_KEY] == chat.id
    assert state[app.PENDING_PROJECT_SELECT_KEY] == project.path
    assert state.canvas_navigation_source == (project, chat)
    assert "save failed" in state.canvas_navigation_error


@pytest.mark.parametrize("result", [RuntimeError("disconnected"), {"ok": False}, {"ok": True, "payload": {}}])
def test_failed_prepare_releases_browser_barrier(monkeypatch, result):
    broker = Mock()
    if isinstance(result, Exception):
        broker.call.side_effect = result
    else:
        broker.call.return_value = result
    monkeypatch.setattr(canvas_runtime, "CANVAS_BROKER", broker)
    with pytest.raises(RuntimeError):
        canvas_runtime.prepare_canvas_leave("canvas-source", "session-a")
    broker.cancel_leave.assert_called_once_with(
        broker.navigation_connection.return_value, broker.call.call_args.kwargs["request_id"]
    )


@pytest.mark.parametrize("document", [None, {"store": {"page:p": {"id": "page:p", "typeName": "page"}}}])
def test_leave_response_requires_a_durable_document(tmp_path, monkeypatch, document):
    import asyncio
    from codex_nomad_surface import canvas_store

    monkeypatch.setattr(canvas_store, "CANVAS_ROOT", tmp_path / "canvases")
    manifest = canvas_store.initialize_canvas("thread-leave")
    canvas_id = manifest["canvas_id"]
    broker = canvas_runtime.CanvasBroker()
    pending = canvas_runtime.PendingCanvasRequest(context={"method": "prepare_leave"})
    broker._pending["leave"] = pending
    asyncio.run(canvas_runtime._handle_canvas_message(
        broker, canvas_id, None,
        {"type": "response", "id": "leave", "ok": True,
         "payload": {} if document is None else {"document": document}},
    ))
    assert pending.event.is_set()
    assert pending.result["ok"] is (document is not None)
    if document is not None:
        assert canvas_store.load_canvas_document(canvas_id) == document
        assert pending.result["payload"]["revision"] == 1
    else:
        assert canvas_store.read_canvas_manifest(canvas_id)["current_revision"] == 0


def test_navigation_does_not_send_to_another_session(monkeypatch):
    async def scenario():
        broker = canvas_runtime.CanvasBroker()
        monkeypatch.setattr(canvas_runtime, "CANVAS_BROKER", broker)
        broker.register("canvas", "tab-a", 1, navigation_owner="session-a")
        active = broker.register("canvas", "tab-b", 1, navigation_owner="session-b")
        send = Mock(wraps=active.outgoing.put)
        monkeypatch.setattr(active.outgoing, "put", send)
        with pytest.raises(RuntimeError, match="canvas_connection_replaced"):
            canvas_runtime.prepare_canvas_leave("canvas", "session-a")
        send.assert_not_called()
    asyncio.run(scenario())


def test_navigation_checks_connection_again_before_sending(monkeypatch):
    async def scenario():
        broker = canvas_runtime.CanvasBroker()
        first = broker.register("canvas", "tab-a", 1, navigation_owner="session-a")
        selected = broker.navigation_connection("canvas", "session-a")
        active = broker.register("canvas", "tab-a", 2, navigation_owner="session-a")
        send = Mock(wraps=active.outgoing.put)
        monkeypatch.setattr(active.outgoing, "put", send)
        with pytest.raises(RuntimeError, match="canvas_connection_replaced"):
            broker.call("canvas", "prepare_leave", expected_connection=selected)
        assert selected is first
        send.assert_not_called()
    asyncio.run(scenario())


def test_replacement_during_save_cancels_only_original_request(monkeypatch):
    async def scenario():
        broker = canvas_runtime.CanvasBroker()
        monkeypatch.setattr(canvas_runtime, "CANVAS_BROKER", broker)
        first = broker.register("canvas", "tab-a", 1, navigation_owner="session-a")
        leave = asyncio.create_task(asyncio.to_thread(
            canvas_runtime.prepare_canvas_leave, "canvas", "session-a"
        ))
        request = await asyncio.wait_for(first.outgoing.get(), 2)
        active = broker.register("canvas", "tab-b", 1, navigation_owner="session-b")
        send = Mock(wraps=active.outgoing.put)
        monkeypatch.setattr(active.outgoing, "put", send)
        with pytest.raises(RuntimeError, match="canvas_connection_replaced"):
            await asyncio.wait_for(leave, 2)
        assert await first.outgoing.get() is None
        assert await first.outgoing.get() == {
            "type": "cancel_leave", "id": request["request"]["id"]
        }
        send.assert_not_called()
    asyncio.run(scenario())


def test_navigation_timeout_cancels_the_timed_out_request(monkeypatch):
    async def scenario():
        broker = canvas_runtime.CanvasBroker()
        monkeypatch.setattr(canvas_runtime, "CANVAS_BROKER", broker)
        monkeypatch.setattr(canvas_runtime, "CANVAS_TOOL_TIMEOUT_SECONDS", 0.01)
        connection = broker.register("canvas", "tab-a", 1, navigation_owner="session-a")
        with pytest.raises(TimeoutError):
            await asyncio.to_thread(canvas_runtime.prepare_canvas_leave, "canvas", "session-a")
        request = await connection.outgoing.get()
        assert await connection.outgoing.get() == {
            "type": "cancel_leave", "id": request["request"]["id"]
        }
        assert not broker._pending
    asyncio.run(scenario())
