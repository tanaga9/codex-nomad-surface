import asyncio
import json

import pytest
from streamlit.testing.v1 import AppTest

from codex_nomad_surface.codex_client import CodexClient
from codex_nomad_surface.mcp_elicitation import enum_options, form_content, form_schema


SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "title": "Name", "minLength": 1},
        "count": {"type": "integer", "minimum": 1, "maximum": 5},
        "enabled": {"type": "boolean"},
        "color": {"type": "string", "oneOf": [
            {"const": "red", "title": "Red"}, {"const": "blue", "title": "Blue"},
        ]},
        "tags": {"type": "array", "items": {"type": "string", "enum": ["a", "b"]}, "minItems": 1},
        "note": {"type": "string"},
    },
    "required": ["name", "count", "enabled", "color", "tags"],
}


def request(mode="form", **params):
    return {"id": 1, "method": "mcpServer/elicitation/request", "params": {
        "mode": mode, "threadId": "thread", "turnId": "turn", "serverName": "server",
        "message": "Provide details", "requestedSchema": SCHEMA, **params,
    }}


def test_typed_form_content_and_optional_fields():
    schema = form_schema(SCHEMA)
    assert form_content(schema, {
        "name": "Team", "count": "2", "enabled": False, "color": "blue", "tags": ["a"], "note": "",
    }) == {"name": "Team", "count": 2, "enabled": False, "color": "blue", "tags": ["a"]}
    assert enum_options(schema["properties"]["color"]) == [("red", "Red"), ("blue", "Blue")]


@pytest.mark.parametrize("values", [
    {}, {"name": ""}, {"count": "2.5"}, {"count": "6"}, {"tags": []}, {"color": "green"},
])
def test_invalid_form_content_is_not_accepted(values):
    complete = {"name": "Team", "count": "2", "enabled": True, "color": "red", "tags": ["a"]}
    complete = {} if not values else {**complete, **values}
    with pytest.raises(ValueError):
        form_content(form_schema(SCHEMA), complete)


def test_nullable_metadata_and_titled_multiselect():
    schema = form_schema({"type": "object", "required": None, "properties": {
        "tags": {"type": "array", "maxItems": None, "items": {"anyOf": [
            {"const": "a", "title": "Alpha"}, {"const": "b", "title": "Beta"},
        ]}},
    }})
    assert form_content(schema, {"tags": ["b"]}) == {"tags": ["b"]}
    assert enum_options(schema["properties"]["tags"]["items"])[1] == ("b", "Beta")


@pytest.mark.parametrize("field", [
    {"type": "object"}, {"type": ["string", "null"]},
    {"$ref": "https://example.com/schema"},
])
def test_unsupported_schema_is_rejected_without_fetching(field):
    with pytest.raises(ValueError):
        form_schema({"type": "object", "properties": {"field": field}})


def test_protocol_responses_and_validation():
    client = CodexClient("ws://unused")
    approval = client._approval_from_message(request())
    with pytest.raises(ValueError):
        client._approval_response_result(approval, "approve")
    with pytest.raises(ValueError):
        client._approval_response_result(approval, 'responseJson:{"action":"accept","content":{}}')
    assert client._approval_response_result(approval, "reject") == {"action": "decline", "content": None}
    assert client._approval_response_result(approval, "cancel") == {"action": "cancel", "content": None}
    url = client._approval_from_message(request("url", url="https://example.com/login"))
    assert client._approval_response_result(url, "approve") == {"action": "accept", "content": None}


def test_submitted_content_reaches_live_receiver():
    async def run():
        class Socket:
            sent = []

            async def send(self, payload):
                self.sent.append(json.loads(payload))

        client = CodexClient("ws://unused")
        content = {"name": "Team", "count": 2, "enabled": False, "color": "blue", "tags": ["a"]}
        approval = client._approval_from_message(request())
        approval["response_state"] = "pending"
        socket = Socket()
        runtime = {"websocket": socket, "pending_requests": {1: approval}}
        response = {"action": "accept", "content": content}
        await client._respond_chat_turn_ws(runtime, approval, "responseJson:" + json.dumps(response))
        assert socket.sent == [{"id": 1, "result": response}]
    asyncio.run(run())


def form_app(message):
    source = '''
import streamlit as st
from codex_nomad_surface import app
from codex_nomad_surface.codex_client import CodexClient
client = CodexClient("ws://unused")
for name, value in (("approval_action_queued", None), ("approval_action_in_progress", "")):
    st.session_state.setdefault(name, value)
approval = client._approval_from_message(MESSAGE)
app.render_pending_requests(client, None, {"requests": [approval]})
'''.replace("MESSAGE", repr(message))
    return AppTest.from_string(source).run()


def capture_responses(monkeypatch):
    from codex_nomad_surface import app

    sent = []
    monkeypatch.setattr(app, "start_approval_response_worker", lambda client, chat, pending, approval, decision: sent.append(decision))
    return sent


def test_form_submits_typed_values_on_first_click(monkeypatch):
    sent = capture_responses(monkeypatch)
    at = form_app(request())
    assert not at.exception
    at.text_input[0].input("Team")
    at.text_input[1].input("2")
    at.selectbox[0].set_value(False)
    at.selectbox[1].set_value("blue")
    at.multiselect[0].set_value(["a", "b"])
    at.button[0].click().run()
    assert not at.exception
    assert json.loads(sent[0].removeprefix("responseJson:")) == {
        "action": "accept", "content": {"name": "Team", "count": 2, "enabled": False, "color": "blue", "tags": ["a", "b"]},
    }


def test_required_input_remains_editable_after_validation_failure(monkeypatch):
    sent = capture_responses(monkeypatch)
    at = form_app(request(requestedSchema={"type": "object", "properties": {
        "name": {"type": "string", "minLength": 1},
    }, "required": ["name"]}))
    at.button[0].click().run()
    assert not at.exception and at.error and not sent
    assert not at.text_input[0].disabled
    at.text_input[0].input("Team")
    at.button[0].click().run()
    assert len(sent) == 1


@pytest.mark.parametrize("required", [False, True])
def test_empty_multiselect_is_optional_only_when_not_required(monkeypatch, required):
    sent = capture_responses(monkeypatch)
    schema = {"type": "object", "properties": {
        "tags": {"type": "array", "items": {"type": "string", "enum": ["a", "b"]}, "minItems": 1},
    }, "required": ["tags"] if required else []}
    at = form_app(request(requestedSchema=schema))
    assert not at.exception
    at.button[0].click().run()
    assert not at.exception
    if required:
        assert at.error and not sent
        assert not at.multiselect[0].disabled
        at.multiselect[0].set_value(["a"])
        at.button[0].click().run()
        assert not at.exception
        expected = {"tags": ["a"]}
    else:
        assert not at.error
        expected = {}
    assert len(sent) == 1
    assert json.loads(sent[0].removeprefix("responseJson:")) == {
        "action": "accept", "content": expected,
    }


@pytest.mark.parametrize("label, action", [("Decline", "decline"), ("Cancel", "cancel")])
def test_incomplete_form_can_be_declined_or_cancelled(monkeypatch, label, action):
    sent = capture_responses(monkeypatch)
    at = form_app(request())
    next(button for button in at.button if button.label == label).click().run()
    assert not at.exception
    client = CodexClient("ws://unused")
    assert client._approval_response_result(client._approval_from_message(request()), sent[0]) == {"action": action, "content": None}


def test_url_request_displays_link_and_requires_confirmation(monkeypatch):
    sent = capture_responses(monkeypatch)
    at = form_app(request("url", url="https://example.com/login", elicitationId="e"))
    assert not at.exception and not sent
    assert at.get("link_button")[0].proto.url == "https://example.com/login"
    next(button for button in at.button if button.label == "Confirm completed").click().run()
    assert not at.exception
    assert json.loads(sent[0].removeprefix("responseJson:")) == {"action": "accept", "content": None}


def test_unsupported_form_has_no_enabled_accept_button():
    at = form_app(request(requestedSchema={"type": "object", "properties": {"nested": {"type": "object"}}}))
    assert not at.exception and at.error
    assert at.button[0].disabled
    assert not at.button[1].disabled and not at.button[2].disabled


@pytest.mark.parametrize("url", ["javascript:alert(1)", "https://["])
def test_invalid_url_can_be_declined_without_confirming(url):
    at = form_app(request("url", url=url))
    assert not at.exception and at.error
    assert not at.get("link_button") and at.button[0].disabled
    assert not at.button[1].disabled


def test_other_form_draft_survives_resolving_a_request():
    source = '''
import streamlit as st
from codex_nomad_surface import app
from codex_nomad_surface.codex_client import CodexClient
client = CodexClient("ws://unused")
for name, value in (("approval_action_queued", None), ("approval_action_in_progress", "")):
    st.session_state.setdefault(name, value)
if "requests" not in st.session_state:
    st.session_state.requests = [client._approval_from_message({
        "id": n, "method": "mcpServer/elicitation/request", "params": {
            "mode": "form", "message": "Enter name", "requestedSchema": {
                "type": "object", "properties": {"name": {"type": "string"}},
            },
        },
    }) for n in (1, 2)]
if st.button("Resolve first"):
    st.session_state.requests = st.session_state.requests[1:]
app.render_pending_requests(client, None, {"requests": st.session_state.requests})
'''
    at = AppTest.from_string(source).run()
    at.text_input[1].input("Keep draft").run()
    at.button[0].click().run()
    assert not at.exception and len(at.text_input) == 1
    assert at.text_input[0].value == "Keep draft"
