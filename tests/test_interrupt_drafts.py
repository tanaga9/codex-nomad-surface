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


def test_completed_turn_keeps_interrupt_draft_for_manual_action(monkeypatch) -> None:
    chat = ChatSession.new("/path/to/repo")
    draft_id = "draft-1"
    chat.add_message(
        "user",
        "Follow-up request",
        metadata={
            "kind": "interrupt_draft",
            "draft_id": draft_id,
            "status": app.INTERRUPT_DRAFT_PENDING,
            "run_id": "run-1",
        },
    )
    pending = {
        "run_id": "run-1",
        "chat_id": chat.id,
        "delivery_confirmed": True,
        "input_text": "Original request",
        "outbox_scope": chat.id,
    }
    session_state = SessionStateStub(
        {
            "pending_turn": pending,
            "pending_interrupt_draft": {
                "chat_id": chat.id,
                "draft_id": draft_id,
            },
            "pending_chat_input_restore": None,
            "approval_action_in_progress": "",
            "approval_action_queued": None,
            "chat_history_autoscroll": False,
        }
    )
    monkeypatch.setattr(app.st, "session_state", session_state)
    monkeypatch.setattr(app.st, "rerun", lambda: None)
    monkeypatch.setattr(app, "cleanup_pending_turn_worker", lambda pending: None)
    monkeypatch.setattr(app, "cleanup_pending_uploaded_chat_images", lambda pending: None)
    monkeypatch.setattr(app, "clear_chat_input_outbox", lambda text, scope: None)

    app.handle_turn_result(chat, pending, {"ok": True, "output": "Done"})

    assert session_state.pending_turn is None
    assert session_state.pending_interrupt_draft == {
        "chat_id": chat.id,
        "draft_id": draft_id,
    }
    assert session_state.pending_chat_input_restore is None
    draft = app.find_interrupt_draft_message(chat, draft_id)
    assert draft is not None
    assert draft.metadata["status"] == app.INTERRUPT_DRAFT_PENDING


def test_return_interrupt_draft_to_input_requires_explicit_action(monkeypatch) -> None:
    chat = ChatSession.new("/path/to/repo")
    draft_id = "draft-1"
    chat.add_message(
        "user",
        "Follow-up request",
        metadata={
            "kind": "interrupt_draft",
            "draft_id": draft_id,
            "status": app.INTERRUPT_DRAFT_PENDING,
            "local_images": ["/path/to/image.png"],
            "prompt_text": "Follow-up request\n\n/path/to/image.png",
        },
    )
    session_state = SessionStateStub(
        {
            "pending_interrupt_draft": {
                "chat_id": chat.id,
                "draft_id": draft_id,
            },
            "pending_chat_input_restore": None,
            "chat_history_autoscroll": False,
        }
    )
    cleaned_images = []
    monkeypatch.setattr(app.st, "session_state", session_state)
    monkeypatch.setattr(
        app,
        "cleanup_uploaded_chat_images",
        lambda paths: cleaned_images.extend(paths or []),
    )

    restored = app.return_interrupt_draft_to_input(
        chat, draft_id, "Follow-up request"
    )

    assert restored is True
    assert session_state.pending_chat_input_restore["chat_id"] == chat.id
    assert session_state.pending_chat_input_restore["text"] == "Follow-up request"
    assert session_state.pending_interrupt_draft is None
    assert session_state.chat_history_autoscroll is True
    assert cleaned_images == ["/path/to/image.png"]
    draft = app.find_interrupt_draft_message(chat, draft_id)
    assert draft is not None
    assert draft.metadata["status"] == app.INTERRUPT_DRAFT_RETURNED
    assert draft.metadata["return_reason"] == "user_requested"
    assert "local_images" not in draft.metadata
    assert "prompt_text" not in draft.metadata


def test_completed_interrupt_draft_renders_only_return_button(monkeypatch) -> None:
    chat = ChatSession.new("/path/to/repo")
    rendered_buttons = []

    def button(label: str, **kwargs) -> bool:
        rendered_buttons.append((label, kwargs))
        return False

    monkeypatch.setattr(app.st, "button", button)

    app.render_return_interrupt_draft_button(
        chat,
        "draft-1",
        "Follow-up request",
        key="completed-draft-return",
    )

    assert rendered_buttons == [
        (
            "Return to input",
            {"key": "completed-draft-return", "width": "stretch"},
        )
    ]
