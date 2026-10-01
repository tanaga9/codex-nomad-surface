import pytest
from streamlit.testing.v1 import AppTest

from codex_nomad_surface import app, async_question_ui
from codex_nomad_surface.async_questions import answers_prompt, normalize_questions, repeats_question_controls
from codex_nomad_surface.codex_client import CodexClient, CodexTurnOutput
from codex_nomad_surface.prompt_template_ui import template_state


QUESTIONS = [
    {"title": "Who is this for?", "options": ["Team", "Public"]},
    {"title": "When is it needed?", "options": None},
]


def item(text="", **updates):
    return {"id": "question-item", "type": "agentMessage", "text": text,
            "phase": "commentary", "delivery": "async", "questions": QUESTIONS, **updates}


def snapshot():
    parts = CodexTurnOutput()
    CodexClient("ws://unused")._update_output_parts_from_item(item(), parts)
    return parts.to_snapshot()


def test_question_only_items_survive_streaming_completion_and_history():
    client = CodexClient("ws://unused")
    parts, stream_items = CodexTurnOutput(), {}
    client._update_output_parts({"method": "item/started", "params": {
        "threadId": "thread", "item": item(),
    }}, parts, [], stream_items)
    before = app.normalize_codex_output_parts(parts.to_snapshot())
    assert before["segments"][0]["metadata"]["questions"][0]["options"] == ["Team", "Public"]
    assert before["segments"][0]["metadata"]["thread_id"] == "thread"
    client._update_output_parts({"method": "item/agentMessage/delta", "params": {
        "itemId": "question-item", "delta": "Working while you answer.",
    }}, parts, [], stream_items)
    client._update_output_parts({"method": "item/completed", "params": {"item": {
        "id": "question-item", "type": "agentMessage", "phase": "commentary",
        "text": "Working while you answer.",
    }}}, parts, [], stream_items)
    after = parts.to_snapshot()
    assert len(after["segments"]) == 1
    assert after["segments"][0]["metadata"]["questions"] == before["segments"][0]["metadata"]["questions"]
    history = client._messages_from_turn({"id": "turn", "items": [item()]})
    assert history[0]["metadata"]["codex_output"]["segments"][0]["metadata"]["delivery"] == "async"


def test_normalization_and_answer_composition():
    questions = normalize_questions([None, {"title": ""}, {"title": "Audience", "options": ["Team", "Team", 1]}, QUESTIONS[1]])
    assert questions[0]["options"] == ["Team"]
    with pytest.raises(ValueError):
        answers_prompt(questions, ["Team", ""])
    assert answers_prompt(questions, ["Partners", " Tomorrow "]) == (
        "Answers to your questions:\n\n1. Audience\n\nPartners\n\n2. When is it needed?\n\nTomorrow"
    )


@pytest.mark.parametrize("text,redundant", [
    ("Who is this for?\n- Team\n- Public\nWhen is it needed?", True),
    ("Who is this for?\nTeam\nPublic\nWhen is it needed?", True),
    ("Please choose.\nWho is this for?\n- Team\n- Public\nWhen is it needed?", False),
    ("Who is this for?\n- Team\n- Public", False),
    ("Who is this for?\n- Team\n- Public\nWhen is it needed?\nI will continue working.", False),
])
def test_redundant_question_text_requires_a_complete_exact_listing(text, redundant):
    segment = {"text": text, "metadata": {"questions": QUESTIONS}}
    assert repeats_question_controls(segment) is redundant


def test_duplicate_text_is_preserved_unless_its_form_rendered():
    client, parts = CodexClient("ws://unused"), CodexTurnOutput()
    text = "Who is this for?\n- Team\n- Public\nWhen is it needed?"
    client._update_output_parts_from_item(item(text, phase=None), parts)
    original = parts.to_snapshot()
    assert app.question_display_parts(original, set())["output"] == text
    assert app.question_display_parts(original, {"question-item"})["output"] == ""
    assert original["output"] == text
    assert original["segments"][0]["text"] == text
    # Without a chat/form, rendering must retain the entire original question.
    at = AppTest.from_string('''
from codex_nomad_surface import app
app.render_codex_stream_output(PARTS)
'''.replace("PARTS", repr(original))).run()
    assert not at.exception
    assert any(m.value == text for m in at.markdown)


def test_question_rendering_deduplicates_only_registered_item_ids(simulated_delivery):
    parts = snapshot()
    at = question_app(parts)
    assert not at.exception
    assert at.radio and at.text_area
    # Missing or different identities cannot authorize removal of body text.
    parts["segments"][0]["text"] = "Who is this for?\n- Team\n- Public\nWhen is it needed?"
    parts["segments"][0]["kind"] = "final_answer"
    assert app.question_display_parts(parts, {"another-item"})["output"]
    parts["segments"][0]["item_id"] = ""
    assert app.question_display_parts(parts, set())["output"]


def test_live_progress_stays_open_through_question_text_and_steer_rerun():
    from pathlib import Path
    at = AppTest.from_file(str(Path(__file__).parent / "browser/async_question_app.py")).run()
    button(at, "Show questions").click().run()
    assert not at.exception
    assert at.expander[0].label == "Progress notes" and at.expander[0].proto.expanded
    assert at.expander[1].label == "Questions" and at.expander[1].proto.expanded
    assert not any("Audience?" in m.value or "Deadline?" in m.value for m in at.markdown)
    at.session_state.question_chat.add_message("user", "Answer", {
        "kind": "interrupt_draft", "status": "steered", "run_id": "run", "draft_id": "draft",
    })
    at.run()
    assert not at.exception
    assert at.expander[0].proto.expanded
    at.run()
    assert at.expander[0].proto.expanded


def test_panel_preserves_manual_choice_and_closes_only_at_completion(monkeypatch):
    from types import SimpleNamespace
    from codex_nomad_surface import output_panels
    state = {}
    monkeypatch.setattr(output_panels, "st", SimpleNamespace(
        session_state=state, expander=lambda label, **kwargs: state[kwargs["key"]],
    ))
    assert output_panels.turn_expander("Progress notes", "progress", True)
    state["progress"] = False  # Native expander on_change updates this value.
    assert not output_panels.turn_expander("Progress notes", "progress", True)
    state["progress"] = True
    assert output_panels.turn_expander("Progress notes", "progress", True)
    assert not output_panels.turn_expander("Progress notes", "progress", False)
    state["progress"] = True
    assert output_panels.turn_expander("Progress notes", "progress", False)


def test_completed_question_only_history_closes_panels_without_recovery_notice(monkeypatch):
    monkeypatch.setattr(app, "load_available_skill_defs", lambda *args: [])
    parts = snapshot()
    parts["segments"].insert(0, {
        "kind": "commentary", "text": "Earlier progress", "item_id": "progress",
    })
    at = AppTest.from_string('''
from types import SimpleNamespace
from codex_nomad_surface import app
from codex_nomad_surface.chat_store import ChatSession
app.init_state()
chat = ChatSession.new("/path/to/project")
chat.thread_id = "thread"
chat.add_message("assistant", "", {"codex_output": PARTS, "turn_status": "completed"})
app.render_chat(SimpleNamespace(base_url="unused"), None, chat)
'''.replace("PARTS", repr(parts))).run()
    assert not at.exception
    assert [e.label for e in at.expander] == ["Progress notes", "Questions"]
    assert not any(e.proto.expanded for e in at.expander)
    assert not at.info


@pytest.fixture
def simulated_delivery(monkeypatch):
    def render(metadata):
        import streamlit as st
        state = template_state(metadata)
        pending = state.get("pending_addition")
        if not pending:
            return
        if st.button("Simulate browser insertion", key=f"ack-{state['id']}"):
            receipts = st.session_state.setdefault("receipts", [])
            if pending["token"] not in receipts:
                receipts.append(pending["token"])
                st.session_state.draft = st.session_state.get("draft", "Existing draft") + "\n\n" + pending["text"]
            state["delivered_token"] = pending["token"]
            state.pop("pending_addition")
            st.rerun()
        if st.button("Cancel insertion", key=f"cancel-{state['id']}"):
            state.pop("pending_addition")
            st.rerun()
    monkeypatch.setattr(async_question_ui, "render_template_delivery", render)


def question_app(parts=None):
    source = '''
import streamlit as st
from codex_nomad_surface.async_question_ui import render_async_questions
st.session_state.setdefault("parts", PARTS)
with st.container(key=st.session_state.get("placement", "live")):
    render_async_questions(st.session_state.parts, st.session_state.get("scope", "thread"),
                           active=st.session_state.get("active"))
'''.replace("PARTS", repr(parts or snapshot()))
    return AppTest.from_string(source).run()


def button(at, label):
    return next(b for b in at.button if b.label == label)


def test_multiple_answers_prepare_once_and_survive_turn_completion(simulated_delivery):
    at = question_app()
    assert not at.exception
    assert at.radio[0].value is None
    assert button(at, "Add answers to draft").disabled
    at.radio[0].set_value(0).run()
    at.text_area[0].input("Tomorrow").run()
    button(at, "Add answers to draft").click().run()
    state = next(iter(at.session_state.async_question_drafts.values()))
    pending = template_state(state)["pending_addition"].copy()
    assert "1. Who is this for?\n\nTeam" in pending["text"]
    assert "2. When is it needed?\n\nTomorrow" in pending["text"]
    at.session_state.placement = "history"
    at.run()
    assert not at.exception
    assert at.text_area[0].value == "Tomorrow"
    assert template_state(state)["pending_addition"] == pending
    button(at, "Simulate browser insertion").click().run()
    assert at.session_state.draft.startswith("Existing draft\n\n")
    assert len(at.session_state.receipts) == 1
    assert button(at, "Add answers to draft").disabled
    at.run()
    assert len(at.session_state.receipts) == 1


def test_questions_close_once_after_ack_and_can_reopen_before_completion(simulated_delivery):
    at = question_app()
    at.session_state.active = True
    at.run()
    at.radio[0].set_value(0).run()
    at.text_area[0].input("Tomorrow").run()
    button(at, "Add answers to draft").click().run()
    assert at.expander[0].proto.expanded
    state = next(iter(at.session_state.async_question_drafts.values()))
    assert "collapsed_after_insertion_token" not in state
    button(at, "Simulate browser insertion").click().run()
    assert not at.exception
    assert not at.expander[0].proto.expanded
    assert at.radio[0].value == 0 and at.text_area[0].value == "Tomorrow"
    assert state["collapsed_after_insertion_token"] == state["requested_token"]
    panel_key = at.expander[0].proto.id.split("-", 2)[2]
    at.session_state[panel_key] = True
    at.run()
    assert at.expander[0].proto.expanded
    at.run()
    assert at.expander[0].proto.expanded
    at.session_state.active = False
    at.run()
    assert not at.expander[0].proto.expanded
    assert at.text_area[0].value == "Tomorrow"


def test_custom_answer_and_cancel_preserve_values(simulated_delivery):
    at = question_app()
    at.radio[0].set_value(2).run()
    at.text_area[0].input("Partners").run()
    at.text_area[1].input("Next week").run()
    button(at, "Add answers to draft").click().run()
    button(at, "Cancel insertion").click().run()
    assert not button(at, "Add answers to draft").disabled
    assert at.expander[0].proto.expanded
    assert at.text_area[0].value == "Partners"
    at.session_state.scope = "other-thread"
    at.run()
    assert at.radio[0].value is None
    assert at.text_area[0].value == ""
    at.session_state.scope = "thread"
    at.run()
    assert at.text_area[0].value == "Partners"
    assert at.text_area[1].value == "Next week"


def test_two_question_items_keep_independent_drafts(simulated_delivery):
    parts = snapshot()
    other = {**parts["segments"][0], "item_id": "another-item"}
    parts["segments"].append(other)
    at = question_app(parts)
    at.radio[0].set_value(0).run()
    at.text_area[0].input("First deadline").run()
    at.radio[1].set_value(1).run()
    at.text_area[1].input("Second deadline").run()
    assert not at.exception
    assert at.text_area[0].value == "First deadline"
    assert at.text_area[1].value == "Second deadline"
    at.session_state.placement = "history"
    at.run()
    assert at.radio[0].value == 0
    assert at.radio[1].value == 1
    assert at.text_area[0].value == "First deadline"
    assert at.text_area[1].value == "Second deadline"


def test_live_and_history_render_one_form_and_keep_answers_after_thread_ack(simulated_delivery, monkeypatch):
    monkeypatch.setattr(app, "load_available_skill_defs", lambda *args: [])
    parts = snapshot()
    # An item notification can precede the turn/start acknowledgement that
    # associates the local chat with its App Server thread.
    parts["segments"][0]["metadata"]["thread_id"] = "thread"
    at = AppTest.from_string('''
import streamlit as st
from types import SimpleNamespace
from codex_nomad_surface import app
from codex_nomad_surface.chat_store import ChatSession
st.session_state.setdefault("complete", False)
chat = st.session_state.setdefault("chat", ChatSession.new("/path/to/project"))
parts = PARTS
if not chat.messages:
    chat.add_message("assistant", "", {"codex_output": parts})
if st.session_state.complete:
    chat.thread_id = "thread"
    st.session_state.pending_turn = None
else:
    st.session_state.pending_turn = {"chat_id": chat.id, "output_parts": parts}
app.render_chat(SimpleNamespace(base_url="unused"), None, chat)
if not st.session_state.complete:
    app.render_codex_stream_output(parts, chat)
'''.replace("PARTS", repr(parts))).run()
    assert not at.exception
    assert len(at.radio) == 1 and len(at.text_area) == 1
    widget_ids = (at.radio[0].id, at.text_area[0].id)
    at.radio[0].set_value(1).run()
    at.text_area[0].input("Tomorrow").run()
    button(at, "Add answers to draft").click().run()
    at.session_state.complete = True
    at.run()
    assert not at.exception
    assert len(at.radio) == 1 and at.radio[0].value == 1
    assert at.text_area[0].value == "Tomorrow"
    assert (at.radio[0].id, at.text_area[0].id) == widget_ids
    assert len(at.session_state.async_question_drafts) == 1
    button(at, "Simulate browser insertion").click().run()
    assert len(at.session_state.receipts) == 1


def test_browser_acknowledgement_marks_question_delivery_once(monkeypatch):
    import streamlit as st
    from types import SimpleNamespace

    def component(*args, **kwargs):
        return lambda **call: SimpleNamespace(ack=st.session_state.get("ack"))

    monkeypatch.setattr(st.components.v2, "component", component)
    at = AppTest.from_string('''
import streamlit as st
from codex_nomad_surface.prompt_template_ui import render_template_delivery, template_state
metadata = st.session_state.setdefault("metadata", {})
state = template_state(metadata)
if "initialized" not in st.session_state:
    state["pending_addition"] = {"token": "receipt", "text": "Answers"}
    st.session_state.initialized = True
render_template_delivery(metadata)
''').run()
    assert not at.exception
    state = template_state(at.session_state.metadata)
    assert "delivered_token" not in state
    at.session_state.ack = "stale"
    at.run()
    assert state["pending_addition"]["token"] == "receipt"
    at.session_state.ack = "receipt"
    at.run()
    assert not at.exception
    assert state["delivered_token"] == "receipt"
    assert "pending_addition" not in state


def test_browser_fixture_keeps_widget_identity_through_actual_completion():
    from pathlib import Path

    at = AppTest.from_file(str(Path(__file__).parent / "browser/async_question_app.py")).run()
    assert not at.exception
    assert [e.label for e in at.expander] == ["Progress notes"]
    button(at, "Show questions").click().run()
    ids = (at.radio[0].id, at.text_area[0].id)
    assert [e.label for e in at.expander] == ["Progress notes", "Questions"]
    assert all(e.proto.expanded for e in at.expander)
    assert at.chat_message[1].children[0].type == "flex_container"
    assert at.chat_message[1].children[1].label == "Questions"
    at.radio[0].set_value(1).run()
    at.text_area[0].input("Tomorrow").run()
    at.session_state.deadline = 0
    at.run()
    assert not at.exception
    assert at.session_state.pending_turn is None
    assert (at.radio[0].id, at.text_area[0].id) == ids
    assert at.chat_message[1].children[0].type == "flex_container"
    assert at.chat_message[1].children[1].label == "Questions"
    assert [e.label for e in at.expander] == ["Progress notes", "Reasoning summary", "Questions"]
    assert not any(e.proto.expanded for e in at.expander)
    assert at.radio[0].value == 1
    assert at.text_area[0].value == "Tomorrow"
    for e in at.expander:
        if e.label in {"Progress notes", "Questions"}:
            at.session_state[e.proto.id.split("-", 2)[2]] = True
    at.run()
    assert all(e.proto.expanded for e in at.expander if e.label in {"Progress notes", "Questions"})


def test_new_auxiliary_output_does_not_move_question_panel(simulated_delivery):
    at = AppTest.from_string('''
import streamlit as st
from codex_nomad_surface import app
from codex_nomad_surface.chat_store import ChatSession
st.session_state.setdefault("parts", PARTS)
with st.chat_message("assistant"):
    app.render_codex_stream_output(st.session_state.parts, ChatSession.new("/path/to/project"),
                                  question_scope="thread")
'''.replace("PARTS", repr(snapshot()))).run()
    assert not at.exception
    assert at.chat_message[0].children[1].label == "Questions"
    ids = (at.radio[0].id, at.text_area[0].id)
    at.radio[0].set_value(0).run()
    at.text_area[0].input("Tomorrow").run()
    at.session_state.parts["segments"].append({
        "kind": "commentary", "text": "More progress", "item_id": "progress",
    })
    at.run()
    assert not at.exception
    assert [e.label for e in at.expander] == ["Progress notes", "Questions"]
    assert at.chat_message[0].children[1].label == "Questions"
    assert (at.radio[0].id, at.text_area[0].id) == ids
    assert at.text_area[0].value == "Tomorrow"


def test_recovery_drains_output_before_deduplicating_history_questions(simulated_delivery, monkeypatch):
    monkeypatch.setattr(app, "load_available_skill_defs", lambda *args: [])
    parts = snapshot()
    parts["segments"][0]["text"] = "Working while you answer."
    at = AppTest.from_string('''
import queue
import streamlit as st
from types import SimpleNamespace
from codex_nomad_surface import app
from codex_nomad_surface.chat_store import ChatSession
app.init_state()
parts = PARTS
chat = ChatSession.new("/path/to/project")
chat.thread_id = "thread"
chat.add_message("assistant", "", {"codex_output": parts})
events = queue.Queue()
events.put({"type": "output", "output_parts": parts})
st.session_state.pending_turn = {"chat_id": chat.id, "recovery_only": True,
    "worker_queue": events, "output_parts": {}}
app.render_chat(SimpleNamespace(base_url="unused"), SimpleNamespace(path=chat.project_path), chat)
'''.replace("PARTS", repr(parts))).run()
    assert not at.exception
    assert len(at.radio) == 1 and len(at.text_area) == 1
