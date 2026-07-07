from __future__ import annotations

from codex_nomad_surface import app
from codex_nomad_surface.chat_store import ChatSession


class SessionStateStub(dict):
    def __getattr__(self, name: str):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name: str, value) -> None:
        self[name] = value


def test_public_query_chat_id_only_exposes_server_threads() -> None:
    assert app.public_query_chat_id("thread:abc123") == "thread:abc123"
    assert app.public_query_chat_id("local-chat-uuid") == ""
    assert app.public_query_chat_id("") == ""


def test_set_query_chat_id_omits_local_chat_ids(monkeypatch) -> None:
    session_state = SessionStateStub()
    query_params = {"chat": "thread:old"}
    monkeypatch.setattr(app.st, "session_state", session_state)
    monkeypatch.setattr(app.st, "query_params", query_params)

    app.set_query_chat_id("local-chat-uuid")

    assert "chat" not in query_params
    assert session_state.last_query_chat_id == ""


def test_url_chat_selection_enables_autoscroll(monkeypatch) -> None:
    session_state = SessionStateStub(
        {
            "last_query_chat_id": "",
            "selected_chat_id": "",
            app.PENDING_CHAT_SELECT_KEY: "",
            "draft_chat": None,
            "chat_history_autoscroll": False,
        }
    )
    query_params = {"chat": "thread:one"}
    selected_project_ids = []
    monkeypatch.setattr(app.st, "session_state", session_state)
    monkeypatch.setattr(app.st, "query_params", query_params)
    monkeypatch.setattr(
        app,
        "select_project_for_chat_id",
        lambda server_threads, chat_id: selected_project_ids.append(chat_id),
    )

    app.sync_chat_selection_from_url([])

    assert session_state.selected_chat_id == "thread:one"
    assert session_state[app.PENDING_CHAT_SELECT_KEY] == "thread:one"
    assert session_state.chat_history_autoscroll is True
    assert selected_project_ids == ["thread:one"]


def test_promote_chat_to_thread_selection_updates_url(monkeypatch) -> None:
    chat = ChatSession.new("/path/to/repo")
    session_state = SessionStateStub(
        {
            "selected_chat_id": chat.id,
            app.PENDING_CHAT_SELECT_KEY: chat.id,
            "codex_run_controls_by_chat": {chat.id: {"model": "gpt-test"}},
            "pending_turn": {"chat_id": chat.id},
            "pending_interrupt_draft": {"chat_id": chat.id},
            "pending_chat_input_restore": {"chat_id": chat.id},
            "last_rendered_chat_id": chat.id,
        }
    )
    old_chat_id = chat.id
    query_params = {}
    monkeypatch.setattr(app.st, "session_state", session_state)
    monkeypatch.setattr(app.st, "query_params", query_params)

    app.promote_chat_to_thread_selection(chat, "thread-1")

    assert chat.id == "thread:thread-1"
    assert session_state.selected_chat_id == "thread:thread-1"
    assert session_state[app.PENDING_CHAT_SELECT_KEY] == "thread:thread-1"
    assert query_params["chat"] == "thread:thread-1"
    assert session_state.last_query_chat_id == "thread:thread-1"
    assert old_chat_id not in session_state.codex_run_controls_by_chat
    assert session_state.codex_run_controls_by_chat["thread:thread-1"] == {
        "model": "gpt-test"
    }
    assert session_state.pending_turn["chat_id"] == "thread:thread-1"
    assert session_state.pending_interrupt_draft["chat_id"] == "thread:thread-1"
    assert session_state.pending_chat_input_restore["chat_id"] == "thread:thread-1"
    assert session_state.last_rendered_chat_id == "thread:thread-1"


def test_promote_chat_to_thread_selection_canonicalizes_other_selected_chat(
    monkeypatch,
) -> None:
    chat = ChatSession.new("/path/to/repo")
    session_state = SessionStateStub(
        {
            "selected_chat_id": "other-chat",
            app.PENDING_CHAT_SELECT_KEY: "other-chat",
            "codex_run_controls_by_chat": {},
        }
    )
    query_params = {}
    monkeypatch.setattr(app.st, "session_state", session_state)
    monkeypatch.setattr(app.st, "query_params", query_params)

    app.promote_chat_to_thread_selection(chat, "thread-1")

    assert chat.id == "thread:thread-1"
    assert session_state.selected_chat_id == "other-chat"
    assert session_state[app.PENDING_CHAT_SELECT_KEY] == "other-chat"
    assert query_params == {}
