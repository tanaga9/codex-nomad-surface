from __future__ import annotations

import asyncio
from typing import Generic, TypeVar


T = TypeVar("T")


class AsyncOutboundQueue(Generic[T]):
    """An event-loop queue that accepts messages from worker threads."""

    def __init__(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue[T] = asyncio.Queue()

    def put(self, message: T) -> bool:
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, message)
        except RuntimeError:
            return False
        return True

    async def get(self) -> T:
        return await self._queue.get()
