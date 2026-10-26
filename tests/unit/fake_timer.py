"""`sleep` falso para código asíncrono: duerme hasta que el test avanza el `FixedClock`."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import datetime, timedelta
from typing import Any

from screening.clock import FixedClock


class FakeTimer:
    def __init__(self, clock: FixedClock) -> None:
        self.clock = clock
        self._waiters: list[tuple[datetime, asyncio.Future[None]]] = []

    async def sleep(self, seconds: float) -> None:
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.append((self.clock.now() + timedelta(seconds=seconds), future))
        await future

    @property
    def waiting(self) -> int:
        return sum(not f.done() for _, f in self._waiters)

    async def advance(self, seconds: float) -> None:
        await asyncio.sleep(0.01)  # las tareas recién creadas registran su espera antes
        self.clock.advance(seconds=seconds)
        for _ in range(10):
            due = [(at, f) for at, f in self._waiters if at <= self.clock.now()]
            self._waiters = [w for w in self._waiters if w not in due]
            for _, future in due:
                if not future.done():
                    future.set_result(None)
            await asyncio.sleep(0.01)


async def eventually(condition: Callable[[], bool], timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)
