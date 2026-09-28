"""Local template/draft integration check; no App Server or outgoing turns."""
from pathlib import Path
import tempfile
import uuid

import streamlit as st

from codex_nomad_surface.prompt_template_ui import (
    render_template_composer, render_template_delivery,
)
from codex_nomad_surface.ui_components import inject_chat_input_bridge
from codex_nomad_surface.prompt_templates import load_templates

if 'template_test_project' not in st.session_state:
    project = Path(tempfile.mkdtemp(prefix='nomad-template-check-'))
    root = project / 'ops/prompts'
    root.mkdir(parents=True)
    (root / 'plain.md').write_text('Literal {braces} </script>\n')
    (root / 'review.md').write_text('Review {{input.target}}\n')
    st.session_state.template_test_project = str(project)

st.title('Prompt Template integration check')
st.caption('Type a draft, insert plain and review templates, then send to verify the received text.')
if st.checkbox("Enable draft bridge", value=st.query_params.get("bridge") != "off"):
    inject_chat_input_bridge()
with st.sidebar:
    if st.button("Use Prompt Template"):
        st.session_state.setdefault('template_test_pickers', []).append({'picker_id': uuid.uuid4().hex})
templates, errors = load_templates(st.session_state.template_test_project)
for error in errors:
    st.warning(error)
for metadata in st.session_state.get('template_test_pickers', []):
    with st.chat_message('prompt-template-picker'):
        render_template_composer(metadata, templates)
value = st.chat_input('Integration draft')
if value is not None:
    st.session_state.received_template_text = value
if 'received_template_text' in st.session_state:
    st.code(st.session_state.received_template_text)
for metadata in st.session_state.get('template_test_pickers', []):
    render_template_delivery(metadata)
