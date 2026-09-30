"""Exercise the turn receiver with interleaved server requests and notifications."""
import asyncio
import json
import queue

import pytest

from codex_nomad_surface.codex_client import CodexClient, CodexTurnOutput


def question(request_id=1, *, blocking=False):
    return {
        "id": request_id,
        "method": "item/tool/requestUserInput",
        "params": {
            "threadId": "thread", "turnId": "turn", "itemId": f"item-{request_id}",
            "isBlocking": blocking,
            "questions": [{"id": "q", "header": "Audience", "question": "Who is this for?",
                           "isOther": True, "isSecret": False,
                           "options": [{"label": "Team", "description": "Internal audience"}]}],
        },
    }


def resolved(request_id=1):
    return {"method": "serverRequest/resolved", "params": {"threadId": "thread", "requestId": request_id}}


def completed(turn_id="turn"):
    return {"method": "turn/completed", "params": {"threadId": "thread", "turn": {"id": turn_id, "status": "completed"}}}


class Socket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.sent = []
        self.closed = False
        self.receiving = False

    def push(self, message):
        self.incoming.put_nowait(message)

    async def recv(self):
        assert not self.receiving, "There must be only one receiver"
        self.receiving = True
        try:
            value = await self.incoming.get()
            if isinstance(value, Exception):
                raise value
            return json.dumps(value)
        finally:
            self.receiving = False

    async def send(self, payload):
        self.sent.append(json.loads(payload))

    async def close(self):
        self.closed = True


def runtime(ws, events):
    return {"websocket": ws, "thread_id": "thread", "turn_id": "turn",
            "loop": asyncio.get_running_loop(), "output_parts": CodexTurnOutput(),
            "approvals": [], "event_callback": events.append}


def test_async_agent_questions_do_not_stop_receiving_or_create_rpc_responses():
    async def run():
        client, ws, events = CodexClient("ws://test"), Socket(), []
        rt = runtime(ws, events)
        questions = [{"title": "Audience?", "options": ["Team", "Public"]}]
        ws.push({"method": "item/started", "params": {
            "threadId": "thread", "turnId": "turn", "item": {
                "type": "agentMessage", "id": "async", "phase": "commentary",
                "delivery": "async", "questions": questions, "text": "",
            }}})
        ws.push({"method": "item/agentMessage/delta", "params": {
            "threadId": "thread", "turnId": "turn", "itemId": "progress", "delta": "Working"}})
        ws.push(completed())
        result = await asyncio.wait_for(client._collect_chat_turn_ws(rt), 1)
        assert result["ok"] and "Working" in result["output"]
        assert result["output_parts"]["segments"][0]["metadata"]["questions"] == questions
        assert not rt["pending_requests"] and not ws.sent
        assert ws.closed
    asyncio.run(run())


@pytest.mark.parametrize("blocking", [False, True])
def test_questions_do_not_stop_progress_or_completion(blocking):
    async def run():
        client, ws, events = CodexClient("ws://test"), Socket(), []
        rt = runtime(ws, events)
        ws.push(question(blocking=blocking))
        ws.push(question(2))
        ws.push({"method": "item/agentMessage/delta", "params": {
            "threadId": "thread", "turnId": "turn", "itemId": "text", "delta": "Working"}})
        ws.push(resolved())
        ws.push(completed())
        result = await asyncio.wait_for(client._collect_chat_turn_ws(rt), 1)
        assert result["ok"] and "Working" in result["output"]
        assert [r["id"] for r in events[1]["requests"]] == [1, 2]
        assert [r["id"] for r in events[2]["requests"]] == [2]
        assert events[-1]["requests"] == []
        assert ws.closed and not ws.sent  # never automatically answer
    asyncio.run(run())


def test_answer_send_and_resolution_race_are_request_scoped():
    async def run():
        client, ws, events = CodexClient("ws://test"), Socket(), []
        rt = runtime(ws, events)
        client._handle_turn_interaction(rt, question())
        client._handle_turn_interaction(rt, question(2))
        first = rt["pending_requests"][1]
        results = await asyncio.gather(
            client._respond_chat_turn_ws(rt, first, 'answersJson:{"q":{"answers":["Team"]}}'),
            client._respond_chat_turn_ws(rt, first, "approve"),
        )
        assert len(ws.sent) == 1
        assert ws.sent[0] == {"id": 1, "result": {"answers": {"q": {"answers": ["Team"]}}}}
        assert results[1]["status"] == "duplicate_approval_response"
        assert rt["pending_requests"][2]["response_state"] == "pending"
        assert events[0]["requests"][0]["response_state"] == "pending"  # snapshots don't mutate
        client._handle_turn_interaction(rt, resolved(2))
        result = await client._respond_chat_turn_ws(rt, {"id": 2}, "approve")
        assert result["status"] == "request_resolved" and len(ws.sent) == 1
        client._handle_turn_interaction(rt, resolved())
        assert not rt["pending_requests"]
    asyncio.run(run())


def test_resolution_during_send_does_not_restore_question():
    async def run():
        client, ws, events = CodexClient("ws://test"), Socket(), []
        rt = runtime(ws, events)
        client._handle_turn_interaction(rt, question())
        async def send(payload):
            client._handle_turn_interaction(rt, resolved())
        ws.send = send
        await client._respond_chat_turn_ws(rt, {"id": 1}, "approve")
        assert not rt["pending_requests"] and events[-1]["requests"] == []
    asyncio.run(run())


def test_public_answer_uses_running_receiver_loop():
    async def run():
        client, ws, events = CodexClient("ws://test"), Socket(), []
        rt = runtime(ws, events)
        client._handle_turn_interaction(rt, question())
        result = await asyncio.to_thread(client.respond_chat_turn, rt, {"id": 1}, "approve")
        assert result["status"] == "response_sent" and len(ws.sent) == 1
    asyncio.run(run())


def test_quiet_turn_keeps_connection_and_can_complete():
    async def run():
        client, ws, events = CodexClient("ws://test"), Socket(), []
        client.TURN_QUIET_NOTICE_SECONDS = 0.001
        rt = runtime(ws, events)
        quiet = asyncio.Event()
        def notify(event):
            events.append(event)
            if event.get("quiet"):
                quiet.set()
        rt["event_callback"] = notify
        task = asyncio.create_task(client._collect_chat_turn_ws(rt))
        await asyncio.wait_for(quiet.wait(), 1)
        assert not task.done() and not ws.closed
        ws.push(completed())
        result = await asyncio.wait_for(task, 1)
        assert result["ok"] and not result["output_parts"]["errors"]
        assert {"type": "activity", "quiet": False} in events
    asyncio.run(run())


def test_start_response_survives_early_question_and_completion():
    async def run():
        client, ws, events = CodexClient("ws://test"), Socket(), []
        rt = runtime(ws, events)
        async def send(payload):
            ws.sent.append(json.loads(payload))
            ws.push(question())
            ws.push(resolved())
            ws.push(completed())
            ws.push({"id": ws.sent[-1]["id"], "result": {"turn": {"id": "turn"}}})
        ws.send = send
        async def handler(message):
            client._handle_turn_interaction(rt, message)
        result = await client._rpc_call(ws, "turn/start", {}, rt["output_parts"], rt["approvals"], message_handler=handler)
        assert result["turn"]["id"] == "turn"
        final = await asyncio.wait_for(client._collect_chat_turn_ws(rt), 1)
        assert final["ok"] and ws.closed
    asyncio.run(run())


def test_unrelated_resolution_and_completion_do_not_end_active_turn():
    async def run():
        client, ws, events = CodexClient("ws://test"), Socket(), []
        rt = runtime(ws, events)
        client._handle_turn_interaction(rt, question())
        other = resolved()
        other["params"]["threadId"] = "other"
        client._handle_turn_interaction(rt, other)
        client._handle_turn_interaction(rt, completed("previous-turn"))
        assert 1 in rt["pending_requests"] and "completed_turn" not in rt
    asyncio.run(run())


def test_ui_drains_progress_and_resolutions_while_questions_are_visible(monkeypatch):
    from codex_nomad_surface import app
    monkeypatch.setattr(app.st, "session_state", {})
    events = queue.Queue()
    pending = {"worker_queue": events, "requests": [{"id": 1}]}
    events.put({"type": "interactions", "requests": [{"id": 1}, {"id": 2}]})
    events.put({"type": "output", "output_parts": {"output": "Still working"}})
    app.drain_pending_turn_events(pending)
    assert len(pending["requests"]) == 2 and "Still working" in pending["output"]
    events.put({"type": "interactions", "requests": [{"id": 2}]})
    app.drain_pending_turn_events(pending)
    assert pending["requests"] == [{"id": 2}]
    events.put({"type": "result", "result": {"ok": True}})
    app.drain_pending_turn_events(pending)
    assert not pending["requests"] and pending["result"]["ok"]


def test_question_forms_keep_other_drafts_when_one_is_resolved():
    from streamlit.testing.v1 import AppTest
    # Real Streamlit widget state, without connecting to Codex.
    source = '''
import streamlit as st
from codex_nomad_surface import app
from codex_nomad_surface.codex_client import CodexClient
client = CodexClient("ws://unused")
for name, value in (("approval_action_queued", None), ("approval_action_in_progress", "")):
    st.session_state.setdefault(name, value)
if "pending" not in st.session_state:
    st.session_state.pending = {"requests": [client._approval_from_message({
        "id": n, "method": "item/tool/requestUserInput", "params": {
            "isBlocking": False, "questions": [{"id": "q", "header": "Audience",
            "question": f"Audience {n}?", "isOther": True, "isSecret": False, "options": None}]
        }}) for n in (1, 2)]}
if st.button("Resolve first"):
    st.session_state.pending["requests"] = st.session_state.pending["requests"][1:]
app.render_pending_requests(client, None, st.session_state.pending)
st.caption("Progress continues")
'''
    at = AppTest.from_string(source).run()
    assert not at.exception
    assert len(at.text_input) == 2
    at.text_input[1].input("Keep this draft").run()
    at.run()
    assert at.text_input[1].value == "Keep this draft"
    at.button[0].click().run()
    assert not at.exception and len(at.text_input) == 1
    assert at.text_input[0].value == "Keep this draft"


def test_ui_response_keeps_original_receiver_queue(monkeypatch):
    from codex_nomad_surface import app
    from codex_nomad_surface.chat_store import ChatSession
    import threading

    finished = threading.Event()
    original_queue = queue.Queue()
    pending = {"worker_queue": original_queue, "worker_id": "receiver", "runtime": {}, "status": "running"}
    class Client:
        def respond_chat_turn(self, runtime, approval, decision):
            finished.set()
            return {"ok": True, "status": "response_sent"}
    app.start_approval_response_worker(Client(), ChatSession.new("/path/to/project"), pending, {"id": 1}, "approve")
    assert finished.wait(1)
    event = original_queue.get(timeout=1)
    assert event["type"] == "response_result"
    assert pending["worker_queue"] is original_queue and pending["worker_id"] == "receiver"
    assert pending["status"] == "running"


@pytest.mark.parametrize("ending", ["completed", "interrupted", "disconnected"])
def test_websocket_turn_can_answer_before_start_acknowledgement(ending):
    """Run the actual worker loop and transport against a local protocol peer."""
    import websockets

    async def run():
        events = asyncio.Queue()
        loop = asyncio.get_running_loop()
        runtime_ready = asyncio.Future()
        received_answers = []

        async def peer(ws):
            init = json.loads(await ws.recv())
            assert init["method"] == "initialize"
            await ws.send(json.dumps({"id": init["id"], "result": {}}))
            assert json.loads(await ws.recv())["method"] == "initialized"
            start = json.loads(await ws.recv())
            assert start["method"] == "thread/start"
            await ws.send(json.dumps({"id": start["id"], "result": {"thread": {"id": "thread"}}}))
            turn = json.loads(await ws.recv())
            assert turn["method"] == "turn/start"
            await ws.send(json.dumps(question(blocking=True)))
            # Withhold turn/start's acknowledgement until the question is answered.
            answer = json.loads(await ws.recv())
            received_answers.append(answer)
            await ws.send(json.dumps(resolved()))
            await ws.send(json.dumps({"id": turn["id"], "result": {"turn": {"id": "turn"}}}))
            await ws.send(json.dumps(question(2)))
            if ending == "disconnected":
                return
            if ending == "interrupted":
                interrupt = json.loads(await ws.recv())
                assert interrupt["method"] == "turn/interrupt"
                await ws.send(json.dumps({"id": interrupt["id"], "result": {}}))
            final = completed()
            final["params"]["turn"]["status"] = ending
            await ws.send(json.dumps(final))

        def on_runtime(rt):
            def deliver():
                if not runtime_ready.done():
                    runtime_ready.set_result(rt)
            loop.call_soon_threadsafe(deliver)

        def on_event(event):
            loop.call_soon_threadsafe(events.put_nowait, event)

        async with websockets.serve(peer, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            client = CodexClient(f"ws://127.0.0.1:{port}")
            worker = asyncio.create_task(asyncio.to_thread(
                client.start_chat_turn, "/path/to/project", "Hello", None,
                runtime_callback=on_runtime, event_callback=on_event,
            ))
            rt = await asyncio.wait_for(runtime_ready, 2)
            event = await asyncio.wait_for(events.get(), 2)
            assert event["requests"][0]["id"] == 1
            assert not worker.done()
            response = await asyncio.to_thread(client.respond_chat_turn, rt, {"id": 1}, "option:0")
            assert response["ok"]
            if ending == "interrupted":
                while True:
                    event = await asyncio.wait_for(events.get(), 2)
                    if any(request["id"] == 2 for request in event.get("requests", [])):
                        break
                interrupted = await asyncio.to_thread(client.interrupt_chat_turn, rt)
                assert interrupted["ok"]
            result = await asyncio.wait_for(worker, 2)
            assert result["ok"] == (ending != "disconnected")
            assert result["turn_id"] == "turn"
            assert not rt["pending_requests"]
            assert received_answers == [{"id": 1, "result": {"answers": {"q": {"answers": ["Team"]}}}}]
    asyncio.run(run())


def test_custom_answer_can_be_sent_on_first_form_submission(monkeypatch):
    from codex_nomad_surface import app
    from streamlit.testing.v1 import AppTest

    sent = []
    monkeypatch.setattr(app, "start_approval_response_worker", lambda client, chat, pending, approval, decision: sent.append(decision))
    request = question()
    source = '''
import streamlit as st
from codex_nomad_surface import app
from codex_nomad_surface.codex_client import CodexClient
client = CodexClient("ws://unused")
for name, value in (("approval_action_queued", None), ("approval_action_in_progress", "")):
    st.session_state.setdefault(name, value)
request = client._approval_from_message(REQUEST)
app.render_pending_requests(client, None, {"requests": [request]})
'''.replace("REQUEST", repr(request))
    at = AppTest.from_string(source).run()
    assert not at.exception and len(at.radio) == 1 and len(at.text_input) == 1
    at.radio[0].set_value("Other")
    at.text_input[0].input("Customers")
    at.button[0].click().run()
    assert not at.exception
    assert sent == ['answersJson:{"q":{"answers":["Customers"]}}']


@pytest.mark.parametrize("phase", ["starting", "running"])
def test_interrupt_failure_uses_same_dispatch_in_both_phases(phase):
    async def run():
        client, ws, events = CodexClient("ws://test"), Socket(), []
        rt = runtime(ws, events)
        client._track_control_request(rt, "interrupt", "turn/interrupt")
        failure = {"id": "interrupt", "error": {"message": "Cannot interrupt yet"}}
        if phase == "starting":
            async def send(raw):
                ws.push(failure)
                ws.push({"id": json.loads(raw)["id"], "result": {"turn": {"id": "turn"}}})
            ws.send = send
            async def dispatch(message):
                client._dispatch_turn_message(rt, message)
            await client._rpc_call(
                ws, "turn/start", {}, rt["output_parts"], rt["approvals"],
                message_handler=dispatch,
            )
        else:
            ws.push(failure)
            ws.push(completed())
            await client._collect_chat_turn_ws(rt)
        assert rt["interrupt_error"] == "Cannot interrupt yet"
        assert rt["output_parts"].to_snapshot()["errors"] == "Cannot interrupt yet"
        assert not rt["control_request_ids"]
        assert not rt["control_request_actions"]
    asyncio.run(run())


def tool_call(request_id):
    return {"id": request_id, "method": "item/tool/call", "params": {
        "threadId": "thread", "turnId": "turn", "tool": "read_scene",
        "arguments": {"request_id": request_id},
    }}


@pytest.mark.parametrize("phase", ["starting", "running"])
def test_tool_wait_does_not_hide_questions_and_tools_stay_serial(phase):
    import threading

    async def run():
        client, ws, events = CodexClient("ws://test"), Socket(), []
        rt = runtime(ws, events)
        entered, release = threading.Event(), threading.Event()
        observed = asyncio.Event()
        tool_replies = asyncio.Event()
        calls = []
        def tool(params):
            calls.append(params["arguments"]["request_id"])
            if len(calls) == 1:
                entered.set()
                assert release.wait(2)
            return {"success": True, "contentItems": []}
        def event_callback(event):
            events.append(event)
            if event.get("requests"):
                observed.set()
        rt["dynamic_tool_handler"] = tool
        rt["event_callback"] = event_callback
        original_send = ws.send
        async def send(raw):
            await original_send(raw)
            if "result" in json.loads(raw):
                if len([item for item in ws.sent if "result" in item]) == 2:
                    tool_replies.set()
        ws.send = send
        async def dispatch(message):
            client._dispatch_turn_message(rt, message)
        if phase == "starting":
            task = asyncio.create_task(client._rpc_call(
                ws, "turn/start", {}, rt["output_parts"], rt["approvals"],
                message_handler=dispatch,
            ))
        else:
            task = asyncio.create_task(client._collect_chat_turn_ws(rt))
        try:
            ws.push(tool_call("tool-1"))
            assert await asyncio.to_thread(entered.wait, 1)
            ws.push(tool_call("tool-2"))
            ws.push(question())
            await asyncio.wait_for(observed.wait(), 1)
            assert calls == ["tool-1"]  # second tool has not started
            assert not task.done() and not release.is_set()
            release.set()
            await asyncio.wait_for(tool_replies.wait(), 1)
            assert calls == ["tool-1", "tool-2"]
            replies = [item for item in ws.sent if "result" in item]
            assert [reply["id"] for reply in replies] == ["tool-1", "tool-2"]
            if phase == "starting":
                ws.push({"id": ws.sent[0]["id"], "result": {"turn": {"id": "turn"}}})
                await asyncio.wait_for(task, 1)
                await client._close_chat_turn_ws(rt)
            else:
                ws.push(completed())
                await asyncio.wait_for(task, 1)
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await client._close_chat_turn_ws(rt)
    asyncio.run(run())


@pytest.mark.parametrize("ending", ["completed", "closed"])
def test_finish_during_tool_wait_drops_queued_work_and_late_reply(ending):
    import threading

    async def run():
        client, ws, events = CodexClient("ws://test"), Socket(), []
        rt = runtime(ws, events)
        entered, release, exited = threading.Event(), threading.Event(), threading.Event()
        calls = []
        def tool(params):
            calls.append(params["arguments"]["request_id"])
            entered.set()
            try:
                assert release.wait(2)
                return {"success": True, "contentItems": []}
            finally:
                exited.set()
        rt["dynamic_tool_handler"] = tool
        task = asyncio.create_task(client._collect_chat_turn_ws(rt))
        try:
            ws.push(tool_call("tool-1"))
            assert await asyncio.to_thread(entered.wait, 1)
            client._dispatch_turn_message(rt, tool_call("tool-2"))
            worker = rt["tool_task"]
            if ending == "completed":
                ws.push(completed())
                await asyncio.wait_for(task, 1)
            else:
                await client._close_chat_turn_ws(rt)
                # The test socket does not wake its reader when closed.
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            assert worker.done() and ws.closed
            assert "tool_queue" not in rt and "tool_task" not in rt
            release.set()
            assert await asyncio.to_thread(exited.wait, 1)
            await asyncio.sleep(0)
            assert calls == ["tool-1"] and not ws.sent
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await client._close_chat_turn_ws(rt)
    asyncio.run(run())


def test_dynamic_tool_exception_is_returned_without_stopping_receiver():
    async def run():
        client, ws, events = CodexClient("ws://test"), Socket(), []
        rt = runtime(ws, events)
        def tool(_params):
            raise RuntimeError("editor unavailable")
        rt["dynamic_tool_handler"] = tool
        original_send = ws.send
        async def send(raw):
            await original_send(raw)
            ws.push(completed())
        ws.send = send
        ws.push(tool_call("tool-1"))
        result = await asyncio.wait_for(client._collect_chat_turn_ws(rt), 1)
        assert result["ok"]
        assert ws.sent[0]["result"]["success"] is False
        assert "editor unavailable" in ws.sent[0]["result"]["contentItems"][0]["text"]
    asyncio.run(run())


def test_tool_reply_transport_failure_closes_receiver():
    async def run():
        client, ws, events = CodexClient("ws://test"), Socket(), []
        rt = runtime(ws, events)
        rt["dynamic_tool_handler"] = lambda params: {"success": True, "contentItems": []}
        async def send(raw):
            raise OSError("write failed")
        async def close():
            ws.closed = True
            ws.push(OSError("connection closed"))
        ws.send, ws.close = send, close
        ws.push(tool_call("tool-1"))
        with pytest.raises(OSError, match="connection closed"):
            await asyncio.wait_for(client._collect_chat_turn_ws(rt), 1)
        assert ws.closed and rt["closing"]
        assert "tool_task" not in rt and "tool_queue" not in rt
        assert "write failed" in rt["output_parts"].to_snapshot()["errors"]
    asyncio.run(run())
