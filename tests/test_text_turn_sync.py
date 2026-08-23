from codex_nomad_surface import app
from codex_nomad_surface.chat_store import ChatSession
from codex_nomad_surface.settings import Project
from codex_nomad_surface.turn_run import TURN_RUN_STARTING


class SessionStateStub(dict):
    def __getattr__(self, name: str):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name: str, value) -> None:
        self[name] = value


class RecordingClient:
    def __init__(self) -> None:
        self.start_calls = 0

    def start_chat_turn(self, *args, **kwargs):
        self.start_calls += 1
        return {"ok": True, "output": "unexpected"}


def test_text_sync_failure_prevents_delivery_and_restores_prompt(monkeypatch):
    project = Project(name="Project", path="/path/to/project")
    chat = ChatSession.new(project.path)
    chat.surface = "text"
    chat.text_id = "text-1"
    chat.add_message(
        "user",
        "Please revise this",
        metadata={
            "kind": "turn_prompt",
            "run_id": "run-1",
            "delivery_status": "sending",
        },
    )
    pending = {
        "run_id": "run-1",
        "chat_id": chat.id,
        "status": TURN_RUN_STARTING,
        "text": "Please revise this",
        "input_text": "Please revise this",
        "outbox_scope": chat.id,
    }
    session_state = SessionStateStub(
        {
            "pending_turn": pending,
            "pending_chat_input_restore": None,
            "turn_worker_registry": {},
            "approval_action_in_progress": "",
            "approval_action_queued": None,
            "chat_history_autoscroll": False,
        }
    )
    client = RecordingClient()

    def fail_sync(text_id: str):
        raise RuntimeError("text_unavailable")

    monkeypatch.setattr(app.st, "session_state", session_state)
    monkeypatch.setattr(app, "sync_text_for_agent", fail_sync)
    monkeypatch.setattr(app.st, "rerun", lambda: None)
    monkeypatch.setattr(app, "cleanup_pending_uploaded_chat_images", lambda pending: None)

    app.start_turn_run_worker(client, project, chat, pending)
    worker = session_state.turn_worker_registry[pending["worker_id"]]
    worker.join(timeout=2)
    assert not worker.is_alive()

    app.drain_pending_turn_events(pending)
    result = pending.pop("result")
    assert client.start_calls == 0
    assert result == {
        "ok": False,
        "output": "[editor sync error] text_unavailable",
    }
    assert pending["editor_sync"] == {
        "status": "failed",
        "text_id": "text-1",
        "error": "text_unavailable",
    }

    app.handle_turn_result(chat, pending, result)

    assert session_state.pending_chat_input_restore["chat_id"] == chat.id
    assert session_state.pending_chat_input_restore["text"] == "Please revise this"
    assert chat.messages[0].metadata["delivery_status"] == "failed"


def test_missing_text_id_fails_closed_before_agent_delivery(monkeypatch):
    project = Project(name="Project", path="/path/to/project")
    chat = ChatSession.new(project.path)
    chat.surface = "text"
    pending = {
        "run_id": "run-missing-text",
        "chat_id": chat.id,
        "status": TURN_RUN_STARTING,
        "text": "Please revise this",
        "input_text": "Please revise this",
        "outbox_scope": chat.id,
    }
    session_state = SessionStateStub(
        {"turn_worker_registry": {}, "pending_turn": pending}
    )
    client = RecordingClient()
    sync_calls: list[str] = []
    monkeypatch.setattr(app.st, "session_state", session_state)
    monkeypatch.setattr(
        app,
        "sync_text_for_agent",
        lambda text_id: sync_calls.append(text_id),
    )

    app.start_turn_run_worker(client, project, chat, pending)
    worker = session_state.turn_worker_registry[pending["worker_id"]]
    worker.join(timeout=2)
    assert not worker.is_alive()

    app.drain_pending_turn_events(pending)
    assert client.start_calls == 0
    assert sync_calls == []
    assert pending["result"] == {
        "ok": False,
        "output": "[editor sync error] text_not_found",
    }
    assert pending["editor_sync"] == {
        "status": "failed",
        "text_id": "",
        "error": "text_not_found",
    }
