import asyncio

import pytest

from codex_nomad_surface.async_outbound import AsyncOutboundQueue
from codex_nomad_surface.canvas_runtime import CanvasBroker
from codex_nomad_surface.text_runtime import TextBroker


def test_async_outbound_queue_preserves_worker_thread_order():
    async def scenario() -> None:
        outgoing = AsyncOutboundQueue[int]()

        def enqueue() -> None:
            assert outgoing.put(1)
            assert outgoing.put(2)
            assert outgoing.put(3)

        await asyncio.to_thread(enqueue)
        assert [await outgoing.get() for _ in range(3)] == [1, 2, 3]

    asyncio.run(scenario())


def test_async_outbound_queue_rejects_messages_after_its_loop_closes():
    async def create_queue() -> AsyncOutboundQueue[str]:
        return AsyncOutboundQueue()

    outgoing = asyncio.run(create_queue())

    assert outgoing.put("late") is False


@pytest.mark.parametrize(
    ("broker_factory", "editor_id", "error"),
    [
        (TextBroker, "text-closed-loop", "text_unavailable"),
        (CanvasBroker, "canvas-closed-loop", "canvas_unavailable"),
    ],
)
def test_broker_does_not_retain_request_when_outbound_loop_is_closed(
    broker_factory, editor_id, error
):
    async def create_broker():
        broker = broker_factory()
        connection = broker.register(editor_id)
        if isinstance(broker, TextBroker):
            assert broker.activate(editor_id, connection)
        return broker

    broker = asyncio.run(create_broker())

    with pytest.raises(RuntimeError, match=error):
        broker.call(editor_id, "read")
    assert broker._pending == {}
