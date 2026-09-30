import asyncio

import pytest
from streamlit.testing.v1 import AppTest

from codex_nomad_surface import app
from codex_nomad_surface.codex_client import CodexClient, CodexModelListResult


@pytest.mark.parametrize("thread_id", [None, "existing"])
@pytest.mark.parametrize("verbosity", ["low", "medium", "high"])
def test_verbosity_is_sent_only_when_creating_a_thread(thread_id, verbosity):
    controls = {
        "verbosity": verbosity,
        "model": "test-model",
        "reasoning_effort": "high",
        "model_provider": "openai",
        "service_tier": "fast",
    }
    thread_overrides = (
        app.build_start_thread_overrides(controls)
        if thread_id is None
        else app.build_continuation_thread_overrides(controls)
    )
    client = CodexClient("ws://unused")
    calls = []

    class Socket:
        async def close(self):
            pass

    async def connect(_):
        return Socket()

    async def initialize(*args, **kwargs):
        return {}

    async def rpc(_socket, method, params, *args, **kwargs):
        calls.append(method)
        assert "verbosity" not in params
        if method in {"thread/start", "thread/resume"}:
            assert params["serviceTier"] == "fast"
            if thread_id is None:
                assert params["config"] == {"model_verbosity": verbosity}
                assert params["modelProvider"] == "openai"
            else:
                assert "config" not in params
                assert "modelProvider" not in params
            return {"thread": {"id": "existing"}}
        assert method == "turn/start"
        assert "config" not in params
        assert params["model"] == "test-model"
        assert params["effort"] == "high"
        return {"turn": {"id": "turn"}}

    async def collect(runtime):
        return {"ok": True}

    client._connect_ws = connect
    client._initialize_ws = initialize
    client._rpc_call = rpc
    client._collect_chat_turn_ws = collect
    result = asyncio.run(client._start_chat_turn_ws(
        "/path/to/project", "Hello", thread_id, None,
        thread_overrides=thread_overrides,
        turn_overrides=app.build_turn_overrides(controls),
    ))
    assert result["ok"]
    assert calls == ["thread/start" if thread_id is None else "thread/resume", "turn/start"]


@pytest.mark.parametrize("verbosity", ["", "invalid"])
def test_unset_or_invalid_verbosity_does_not_override_codex_config(verbosity):
    controls = {"verbosity": verbosity}
    assert "config" not in app.build_start_thread_overrides(controls)
    assert "config" not in app.build_continuation_thread_overrides(controls)
    assert "verbosity" not in app.build_turn_overrides(controls)


@pytest.fixture
def stub_codex_catalog(monkeypatch):
    monkeypatch.setattr(app, "load_codex_config", lambda _: {
        "model": "test-model", "model_provider": "openai", "model_verbosity": "high",
    })
    monkeypatch.setattr(app, "load_codex_model_list", lambda _: CodexModelListResult([
        {"id": "test-model", "supportedReasoningEfforts": []},
    ]))


def run_overrides_app():
    return AppTest.from_string('''
import streamlit as st
from codex_nomad_surface import app
from codex_nomad_surface.chat_store import ChatSession
st.session_state.setdefault("chat", ChatSession.new("/path/to/project"))
app.render_codex_run_overrides(
    "ws://unused", st.session_state.chat, "test", False, False,
)
''').run()


def test_new_chat_verbosity_can_be_cleared_before_start(stub_codex_catalog):
    at = run_overrides_app()
    assert not at.exception
    select = at.selectbox(key="test_verbosity")
    assert select.options == ["low", "medium", "high"]
    assert select.value is None
    at.selectbox(key="test_verbosity").select("low").run()
    assert not at.exception
    chat = at.session_state.chat
    assert at.session_state.codex_run_controls_by_chat[chat.id]["verbosity"] == "low"
    at.selectbox(key="test_verbosity").set_value(None).run()
    assert not at.exception
    assert "verbosity" not in at.session_state.codex_run_controls_by_chat[chat.id]


def test_existing_chat_has_no_editable_verbosity(stub_codex_catalog):
    at = run_overrides_app()
    at.selectbox(key="test_verbosity").select("low").run()
    chat = at.session_state.chat
    chat.thread_id = "existing"
    at.run()
    assert not at.exception
    assert not any(widget.label == "Verbosity" for widget in at.selectbox)
    assert not any(widget.label == "Verbosity" for widget in at.text_input)
    controls = at.session_state.codex_run_controls_by_chat[chat.id]
    assert "verbosity" not in controls
    assert "config" not in app.build_continuation_thread_overrides(controls)
    assert any(widget.label == "Reasoning effort" for widget in at.selectbox)
