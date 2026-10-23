"""Despacho de lotes: espera la ventana de agrupación y entrega al `InboundHandler`.

Corre fuera de la request del webhook, por eso en Cloud Run exige CPU siempre asignada
(ver spec 005).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from screening.channels import InboundBatch, InboundHandler, InboundMessage, InboundMessageStore
from screening.channels.inbound import MessageBatcher
from screening.clock import Clock

logger = logging.getLogger(__name__)

Sleep = Callable[[float], Awaitable[None]]


class BatchDispatcher:
    def __init__(
        self,
        batcher: MessageBatcher,
        store: InboundMessageStore,
        handler: InboundHandler,
        clock: Clock,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._batcher = batcher
        self._store = store
        self._handler = handler
        self._clock = clock
        self._sleep = sleep
        self._tasks: set[asyncio.Task[None]] = set()

    def submit(self, message: InboundMessage) -> None:
        """Agrega el mensaje a su lote y programa un temporizador. Requiere un event loop."""
        self._batcher.add(message, self._clock.now())
        task = asyncio.create_task(self._flush_when_due(message.sender))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def shutdown(self) -> None:
        """Cancela los temporizadores y entrega de inmediato los lotes pendientes."""
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for batch in self._batcher.pop_all():
            await self._deliver(batch)

    async def _flush_when_due(self, sender: str) -> None:
        # Cada mensaje programa su temporizador; si llegó otro después, el plazo se movió y
        # se vuelve a dormir. El primero que encuentra el lote vencido lo entrega.
        while (remaining := self._batcher.seconds_until_due(sender, self._clock.now())) is not None:
            if remaining <= 0:
                batch = self._batcher.pop(sender)
                if batch is not None:
                    await self._deliver(batch)
                return
            await self._sleep(remaining)

    async def _deliver(self, batch: InboundBatch) -> None:
        try:
            await asyncio.to_thread(self._handler.handle, batch)
        except Exception:
            logger.exception(
                "Falló el handler para el lote de %s; queda sin procesar", batch.sender
            )
            return
        self._store.mark_processed([m.external_id for m in batch.messages])
