"""Standalone browser integration check; never connects to Codex App Server."""

from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
st.title("Chat drag integration check")
st.caption("Run the check, then send the draft to verify uploaded file contents on the server.")
value = st.chat_input("Integration draft", accept_file="multiple")
if value is not None:
    expected = [("drag-regression.txt", b"drag regression content")]
    received = [(item.name, item.getvalue()) for item in value.files]
    if received == expected and value.text == "Preserve this draft":
        st.success("PASS: draft and uploaded file contents received by Streamlit")
    else:
        st.error("FAIL: unexpected draft or uploaded file contents")

st.html(
    "<script>" + (ROOT / "codex_nomad_surface/ui_components/assets/chat_input_drag_guard.js").read_text() + "</script>",
    unsafe_allow_javascript=True,
)
st.html(
    (Path(__file__).with_name("chat_input_drag_check.html")).read_text(),
    unsafe_allow_javascript=True,
)
