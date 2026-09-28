from streamlit.testing.v1 import AppTest


def template_app():
    import streamlit as st
    from types import SimpleNamespace
    from codex_nomad_surface.app import sidebar_prompt_template_actions, replace_chat_id
    from codex_nomad_surface.chat_store import ChatSession
    from codex_nomad_surface.prompt_template_ui import (
        acknowledge_delivery, render_template_composer, template_state,
    )
    project = SimpleNamespace(path=st.session_state['project'])
    chat_key = st.session_state.get('chat', 'first')
    chats = st.session_state.setdefault('test_chats', {})
    if chat_key not in chats:
        chat = ChatSession.new(project.path)
        chat.thread_id = 'existing'
        chat.add_message('user', 'Earlier turn')
        chats[chat_key] = chat
    chat = chats[chat_key]
    st.session_state.pending_turn = st.session_state.get('test_pending')
    if st.button('Promote chat'):
        replace_chat_id(chat, 'thread:promoted')
        st.rerun()
    if st.button('Browser confirms insertion'):
        for message in chat.messages:
            if message.role == 'prompt_template_picker':
                state = template_state(message.metadata)
                pending = state.get('pending_addition')
                if pending and acknowledge_delivery(state, pending['token']):
                    st.session_state.setdefault('insertions', []).append(pending['text'])
    with st.sidebar:
        sidebar_prompt_template_actions(project, chat)
    from codex_nomad_surface.prompt_templates import load_templates
    templates, errors = load_templates(project.path)
    for error in errors:
        st.warning(error)
    states = []
    for message in chat.messages:
        if message.role == 'prompt_template_picker':
            with st.chat_message('prompt-template-picker'):
                render_template_composer(message.metadata, templates)
            states.append(template_state(message.metadata))
    st.session_state['picker_states'] = states


def button(app, label, index=0):
    return [item for item in app.button if item.label == label][index]


def choose(app, template_id, index=0):
    while len(app.selectbox) <= index:
        button(app, 'Use Prompt Template').click().run()
    app.selectbox[index].select(template_id).run()


def setup(tmp_path):
    root = tmp_path / 'ops/prompts'
    root.mkdir(parents=True)
    (root / 'plain.md').write_text('  Literal {value}\n')
    (root / 'variable.md').write_text('{{input.target}}\n')
    app = AppTest.from_function(template_app)
    app.session_state['project'] = str(tmp_path)
    app.run()
    assert not app.exception
    return app, root


def test_insertion_retains_picker_and_values_for_reuse(tmp_path):
    app, _ = setup(tmp_path)
    choose(app, 'variable.md')
    assert not any(b.label == 'Close' for b in app.button)
    assert button(app, 'Add to draft').disabled
    app.text_input[0].set_value('chosen').run()
    button(app, 'Add to draft').click().run()
    pending = app.session_state['picker_states'][0]['pending_addition'].copy()
    app.run()
    assert app.session_state['picker_states'][0]['pending_addition'] == pending
    button(app, 'Browser confirms insertion').click().run()
    assert app.session_state['insertions'] == ['chosen\n']
    assert app.selectbox[0].value == 'variable.md'
    assert app.text_input[0].value == 'chosen'
    assert not button(app, 'Add to draft').disabled
    button(app, 'Add to draft').click().run()
    assert app.session_state['picker_states'][0]['pending_addition']['token'] != pending['token']
    assert not app.exception


def test_multiple_pickers_have_independent_inputs(tmp_path):
    app, _ = setup(tmp_path)
    choose(app, 'variable.md')
    app.text_input[0].set_value('first').run()
    choose(app, 'variable.md', index=1)
    assert app.text_input[0].value == 'first'
    assert app.text_input[1].value == ''
    app.text_input[1].set_value('second').run()
    button(app, 'Add to draft', 1).click().run()
    assert 'pending_addition' not in app.session_state['picker_states'][0]
    assert app.session_state['picker_states'][1]['pending_addition']['text'] == 'second\n'
    button(app, 'Browser confirms insertion').click().run()
    assert len(app.selectbox) == 2
    assert [field.value for field in app.text_input] == ['first', 'second']
    assert not app.exception


def test_inputs_do_not_leak_between_chats_and_reset_on_file_changes(tmp_path):
    app, root = setup(tmp_path)
    choose(app, 'variable.md')
    app.text_input[0].set_value('unfinished').run()
    app.session_state['chat'] = 'other'
    app.run()
    choose(app, 'variable.md')
    assert app.text_input[0].value == ''
    app.session_state['chat'] = 'first'
    app.run()
    assert app.text_input[0].value == 'unfinished'
    (root / 'variable.md').write_text('Changed {{input.target}}')
    app.run()
    assert app.text_input[0].value == ''
    assert app.info


def test_chat_id_promotion_preserves_inputs_and_pending_delivery(tmp_path):
    app, _ = setup(tmp_path)
    choose(app, 'variable.md')
    app.text_input[0].set_value('unfinished').run()
    button(app, 'Add to draft').click().run()
    pending = app.session_state['picker_states'][0]['pending_addition'].copy()
    button(app, 'Promote chat').click().run()
    assert app.text_input[0].value == 'unfinished'
    assert app.session_state['picker_states'][0]['pending_addition'] == pending
    button(app, 'Browser confirms insertion').click().run()
    assert app.session_state['insertions'] == ['unfinished\n']
    assert not app.exception


def test_stale_ack_does_not_clear_new_delivery():
    from codex_nomad_surface.prompt_template_ui import acknowledge_delivery
    state = {'pending_addition': {'token': 'new', 'template_id': 't'},
             'drafts': {'t': {'values': {'x': 'keep'}}}}
    assert not acknowledge_delivery(state, 'old')
    assert state['drafts']['t']['values']['x'] == 'keep'


def test_sidebar_disabled_during_turns_and_available_afterward(tmp_path):
    app, _ = setup(tmp_path)
    choose(app, 'plain.md')
    for pending in (None, {'status': 'running'}, {'recovery_only': True}, None):
        app.session_state['test_pending'] = pending
        app.run()
        assert button(app, 'Use Prompt Template').disabled == bool(pending)
        button(app, 'Add to draft').click().run()
        assert app.session_state['picker_states'][0]['pending_addition']['text'] == '  Literal {value}\n'
        button(app, 'Browser confirms insertion').click().run()
        assert not app.exception


def test_large_catalog_uses_one_selector_per_picker(tmp_path):
    app, root = setup(tmp_path)
    for index in range(100):
        (root / f'prompt-{index:03}.md').write_text(f'Prompt {index}')
    app.run()
    assert not app.selectbox
    choose(app, 'prompt-099.md')
    assert len(app.selectbox) == 1
    assert len(app.selectbox[0].options) == 102
    button(app, 'Add to draft').click().run()
    assert app.session_state['picker_states'][0]['pending_addition']['text'] == 'Prompt 99'


def test_history_trim_preserves_local_pickers_and_pending_text():
    app = AppTest.from_string('''
import streamlit as st
from types import SimpleNamespace
from unittest.mock import patch
from codex_nomad_surface import app
from codex_nomad_surface.chat_store import ChatSession, ChatMessage
chat = ChatSession.new('/path/to/project')
chat.thread_id = 'thread'
for i in range(51):
    chat.add_message('user', str(i))
for role in ('skill_picker', 'file_path_picker', 'prompt_template_picker'):
    chat.add_message(role, '', {'picker_id': role})
picker = chat.messages[-1]
picker.metadata['template_state'] = {'pending_addition': {'text': 'Keep me', 'token': 'pending'}}
st.session_state.chat_history_autoscroll = True
st.session_state.pending_turn = None
client = SimpleNamespace(read_thread_messages=lambda *args, **kwargs: {})
with patch.object(app, 'thread_messages_from_result', return_value=[ChatMessage('assistant', 'Recent')]), patch.object(app, 'update_thread_history_state'):
    assert app.trim_chat_history_if_needed(client, chat)
assert [m.role for m in chat.messages] == ['skill_picker', 'file_path_picker', 'prompt_template_picker', 'assistant']
assert chat.messages[-2] is picker
assert picker.metadata['template_state']['pending_addition']['text'] == 'Keep me'
''').run()
    assert not app.exception


def shared_catalog_app():
    import streamlit as st
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import patch
    from codex_nomad_surface import app
    from codex_nomad_surface.chat_store import ChatSession
    project = st.session_state.project
    chat = st.session_state.setdefault('catalog_chat', ChatSession.new(project))
    if not chat.messages:
        for index in range(5):
            chat.add_message('prompt_template_picker', '', {'picker_id': str(index)})
    reads = []
    original_open = Path.open
    def count_open(path, *args, **kwargs):
        if str(path).startswith(project) and path.suffix == '.md':
            reads.append(str(path))
        return original_open(path, *args, **kwargs)
    with patch.object(Path, 'open', count_open), patch.object(app, 'load_available_skill_defs', return_value=[]):
        app.render_chat(SimpleNamespace(base_url='unused'), SimpleNamespace(path=project), chat)
    st.session_state['catalog_reads'] = reads


def test_history_reads_catalog_once_for_multiple_pickers_and_refreshes_files(tmp_path):
    root = tmp_path / 'ops/prompts'
    root.mkdir(parents=True)
    for index in range(100):
        (root / f'{index:03}.md').write_text(f'---\ntitle: Prompt {index}\n---\nBody')
    app = AppTest.from_function(shared_catalog_app)
    app.session_state.project = str(tmp_path)
    app.run()
    assert not app.exception
    assert len(app.selectbox) == 5
    assert len(app.session_state['catalog_reads']) == 100
    assert len(set(app.session_state['catalog_reads'])) == 100
    (root / '000.md').write_text('---\ntitle: Updated\n---\nNew body')
    app.run()
    assert len(app.session_state['catalog_reads']) == 100
    assert all(select.options[0].startswith('Updated') for select in app.selectbox)
    assert not app.exception
