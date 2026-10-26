"""Agrupación de mensajes entrantes con una cola de tareas (spec 016).

El webhook encola, por remitente y ventana, una tarea a `POST /tasks/process-inbound`; la
tarea lee de la base los mensajes sin procesar del remitente y los entrega como un lote.
Ningún lote ni temporizador vive en la memoria del proceso.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import math
from datetime import datetime
from enum import StrEnum

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import SecretStr

from screening.channels import (
    CandidateBusyError,
    InboundBatch,
    InboundHandler,
    InboundMessageStore,
)
from screening.clock import Clock
from screening.tasks import TASK_TOKEN_HEADER, TaskQueue

logger = logging.getLogger(__name__)

PROCESS_INBOUND_PATH = "/tasks/process-inbound"
RETRY_AFTER_SECONDS = 5


def inbound_task_id(sender: str, now: datetime, window_seconds: float) -> str:
    """Mismo nombre para todas las llegadas del remitente dentro de un intervalo de la ventana.

    El hash va primero (Cloud Tasks prefiere nombres no secuenciales) y oculta el teléfono.
    """
    digest = hashlib.sha256(sender.encode()).hexdigest()[:16]
    bucket = math.floor(now.timestamp() / window_seconds) if window_seconds > 0 else now.timestamp()
    return f"{digest}-inbound-{int(bucket)}"


class InboundScheduler:
    def __init__(self, queue: TaskQueue, window_seconds: float, clock: Clock) -> None:
        self._queue = queue
        self._window = window_seconds
        self._clock = clock

    def schedule(self, sender: str) -> bool:
        """Encola la tarea del remitente. Llamar después de persistir el mensaje.

        `now` se toma aquí, tras persistir: la tarea del intervalo corre en
        `primera llegada + ventana`, siempre después de que el mensaje está en la base.
        """
        return self._queue.enqueue(
            PROCESS_INBOUND_PATH,
            {"sender": sender},
            delay_seconds=self._window,
            task_id=inbound_task_id(sender, self._clock.now(), self._window),
        )


class ProcessOutcome(StrEnum):
    PROCESSED = "processed"
    EMPTY = "empty"
    BUSY = "busy"
    FAILED = "failed"


class InboundBatchProcessor:
    def __init__(self, store: InboundMessageStore, handler: InboundHandler) -> None:
        self._store = store
        self._handler = handler

    def process(self, sender: str) -> tuple[ProcessOutcome, int]:
        messages = sorted(
            self._store.list_unprocessed_for(sender), key=lambda m: (m.sent_at, m.received_at)
        )
        if not messages:
            return ProcessOutcome.EMPTY, 0
        try:
            self._handler.handle(InboundBatch(sender=sender, messages=messages))
        except CandidateBusyError:
            logger.info("Candidato %s ocupado; la tarea se reintentará", sender)
            return ProcessOutcome.BUSY, len(messages)
        except Exception:
            logger.exception("Falló el handler para el lote de %s; se reintentará", sender)
            return ProcessOutcome.FAILED, len(messages)
        self._store.mark_processed([m.external_id for m in messages])
        return ProcessOutcome.PROCESSED, len(messages)


def build_inbound_tasks_router(processor: InboundBatchProcessor, token: SecretStr) -> APIRouter:
    router = APIRouter()
    expected = token.get_secret_value().encode()

    @router.post(PROCESS_INBOUND_PATH)
    async def process_inbound(request: Request) -> JSONResponse:
        given = request.headers.get(TASK_TOKEN_HEADER)
        if given is None or not hmac.compare_digest(given.encode(), expected):
            return JSONResponse({"error": "invalid task token"}, status_code=401)
        try:
            body = await request.json()
        except ValueError:
            body = None
        sender = body.get("sender") if isinstance(body, dict) else None
        if not isinstance(sender, str) or not sender:
            return JSONResponse({"error": "invalid task payload"}, status_code=400)

        # El handler es bloqueante (LLM, base de datos): corre en un hilo.
        outcome, count = await asyncio.to_thread(processor.process, sender)
        if outcome in (ProcessOutcome.BUSY, ProcessOutcome.FAILED):
            # Cualquier código que no sea 2xx hace reintentar a Cloud Tasks con backoff.
            return JSONResponse(
                {"status": outcome.value},
                status_code=503,
                headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
            )
        if outcome == ProcessOutcome.EMPTY:
            return JSONResponse({"status": outcome.value})
        return JSONResponse({"status": outcome.value, "messages": count})

    return router
