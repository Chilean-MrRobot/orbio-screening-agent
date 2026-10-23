"""Factoría de la app FastAPI (webhook de WhatsApp y salud)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from screening.api.dispatcher import BatchDispatcher, Sleep
from screening.api.settings import WhatsAppSettings
from screening.api.webhook import build_webhook_router
from screening.channels import (
    EchoInboundHandler,
    InboundHandler,
    InboundMessageStore,
    InMemoryInboundMessageStore,
    LoggingInboundHandler,
    MessageBatcher,
    WhatsAppCloudChannel,
)
from screening.clock import Clock, SystemClock


def create_app(
    settings: WhatsAppSettings,
    *,
    store: InboundMessageStore,
    handler: InboundHandler,
    clock: Clock,
    sleep: Sleep = asyncio.sleep,
) -> FastAPI:
    dispatcher = BatchDispatcher(
        MessageBatcher(settings.batch_seconds), store, handler, clock, sleep
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await dispatcher.shutdown()

    app = FastAPI(title="Orbio screening agent", lifespan=lifespan)
    app.state.dispatcher = dispatcher
    app.include_router(build_webhook_router(settings, store, dispatcher, clock))

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


def create_app_from_env() -> FastAPI:
    """Punto de entrada: `uvicorn screening.api:create_app_from_env --factory`.

    Hasta la ola 2 el handler es de prueba: eco por WhatsApp si hay credenciales de envío,
    o solo log si no.
    """
    logging.basicConfig(level=logging.INFO)
    settings = WhatsAppSettings.from_env()
    handler: InboundHandler = LoggingInboundHandler()
    if settings.access_token and settings.phone_number_id:
        channel = WhatsAppCloudChannel(
            settings.access_token.get_secret_value(),
            settings.phone_number_id,
            api_version=settings.api_version,
        )
        handler = EchoInboundHandler(channel)
    return create_app(
        settings, store=InMemoryInboundMessageStore(), handler=handler, clock=SystemClock()
    )
