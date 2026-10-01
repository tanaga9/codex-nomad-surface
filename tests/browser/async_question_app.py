"""Exercise question drafts through turn completion without an App Server."""
import time
from types import SimpleNamespace
from unittest.mock import patch

import streamlit as st

from codex_nomad_surface import app
from codex_nomad_surface.chat_store import ChatSession
from codex_nomad_surface.ui_components import inject_chat_input_bridge

app.init_state()
st.title("Async question draft integration check")
st.caption(
    "Show questions after progress appears. Start the timer, type an answer, "
    "and keep focus in its input until the turn "
    "finishes. The text should remain. Then add the answers to the draft and send "
    "to verify the received text. Reload to repeat. No Codex connection is used."
)
if "question_chat" not in st.session_state:
    chat = ChatSession.new("/path/to/project")
    chat.thread_id = "thread"
    chat.add_message("user", "Ask about audience and deadline.", {"run_id": "run"})
    st.session_state.question_chat = chat
    st.session_state.pending_turn = {
        "chat_id": chat.id, "thread_id": chat.thread_id, "run_id": "run",
        "text": chat.messages[0].content, "status": "running",
        "output_parts": {"segments": [{
            "kind": "commentary", "text": "Working on your request.",
            "item_id": "progress",
        }]},
    }
chat = st.session_state.question_chat
pending = st.session_state.pending_turn
questions_shown = bool(pending and any(
    segment.get("item_id") == "questions"
    for segment in pending["output_parts"]["segments"]
))
if st.button("Show questions", disabled=not pending or questions_shown):
    pending["output_parts"]["segments"].append({
        "kind": "final_answer", "item_id": "questions",
        "text": "Audience?\n\n- Team\n- Public\n\nDeadline?",
        "metadata": {"delivery": "async", "questions": [
            {"title": "Audience?", "options": ["Team", "Public"]},
            {"title": "Deadline?", "options": None},
        ]},
    })
if st.button("Finish turn in 8 seconds", disabled=not st.session_state.pending_turn):
    st.session_state.deadline = time.monotonic() + 8
if st.button("Simulate steer acknowledgement", disabled=not pending):
    chat.add_message("user", "A simulated answer was sent.", {
        "kind": "interrupt_draft", "status": "steered", "run_id": "run", "draft_id": "draft",
    })
inject_chat_input_bridge()


@st.fragment(run_every=0.5)
def panel():
    pending = st.session_state.pending_turn
    if pending and time.monotonic() >= st.session_state.get("deadline", float("inf")):
        parts = pending["output_parts"]
        pending["result"] = {"ok": True, "turn_id": "turn", "output_parts": {
            "segments": [*parts["segments"], {
                "kind": "reasoning_summary", "item_id": "reasoning",
                "text": "Additional output appeared at completion.",
            }, {
                "kind": "final_answer", "item_id": "answer", "text": "Turn completed.",
            }],
        }}
    st.caption("Running" if pending else "Completed")
    client = SimpleNamespace(base_url="unused")
    with patch.object(app, "load_available_skill_defs", return_value=[]):
        app.render_chat(client, None, chat, skip_latest_user=bool(pending))
        app.render_pending_turn(client, None, chat)


panel()
submitted = st.chat_input("Integration draft")
if submitted is not None:
    st.session_state.received = submitted
if "received" in st.session_state:
    st.code(st.session_state.received)
