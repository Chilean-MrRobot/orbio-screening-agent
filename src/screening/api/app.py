"""Factoría de la app FastAPI (webhook de WhatsApp, postulaciones y salud)."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI
from pydantic import SecretStr

from screening.api.applications import ApplicationStarter, build_applications_router
from screening.api.crm import build_crm_router_from_env
from screening.api.dispatcher import BatchDispatcher, Sleep
from screening.api.reengagement import ReengagementRunner, build_reengagement_router
from screening.api.settings import WhatsAppSettings
from screening.api.webhook import build_webhook_router
from screening.api.wiring import build_storage, load_agent, load_summarizer
from screening.channels import (
    ConsoleChannel,
    InboundHandler,
    InboundMessageStore,
    MessageBatcher,
    SimulatedSmsChannel,
    WhatsAppCloudChannel,
)
from screening.clock import Clock, SystemClock
from screening.config import load_config
from screening.conversation import ConversationOrchestrator
from screening.domain import ChannelName
from screening.ports import MessageChannel, ScreeningAgent
from screening.reengagement import ReengagementService


def create_app(
    settings: WhatsAppSettings,
    *,
    store: InboundMessageStore,
    handler: InboundHandler,
    clock: Clock,
    sleep: Sleep = asyncio.sleep,
    applications: ApplicationStarter | None = None,
    reengagement: ReengagementRunner | None = None,
    reengagement_token: SecretStr | None = None,
    crm_router: APIRouter | None = None,
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
    if applications is not None:
        app.include_router(build_applications_router(applications))
    if reengagement is not None:
        app.include_router(build_reengagement_router(reengagement, reengagement_token))
    if crm_router is not None:
        app.include_router(crm_router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


def create_app_from_env(agent: ScreeningAgent | None = None) -> FastAPI:
    """Punto de entrada: `uvicorn screening.api:create_app_from_env --factory`.

    Arma el orquestador real: repositorio según `SCREENING_REPOSITORY`, WhatsApp si hay
    credenciales de envío (si no, consola) y el agente de `load_agent` salvo que se inyecte.
    """
    load_dotenv()  # en local; en Cloud Run las variables vienen del servicio
    logging.basicConfig(level=logging.INFO)
    settings = WhatsAppSettings.from_env()
    config = load_config()
    clock = SystemClock()
    repository, store = build_storage()

    channel: MessageChannel = ConsoleChannel()
    if settings.access_token and settings.phone_number_id:
        channel = WhatsAppCloudChannel(
            settings.access_token.get_secret_value(),
            settings.phone_number_id,
            api_version=settings.api_version,
        )

    orchestrator = ConversationOrchestrator(
        repository=repository,
        agent=agent or load_agent(config, clock),
        channel=channel,
        clock=clock,
        config=config,
        summarizer=load_summarizer(config),
    )
    reengagement = ReengagementService(
        repository=repository,
        channels={ChannelName.WHATSAPP: channel, ChannelName.SMS: SimulatedSmsChannel(clock)},
        clock=clock,
        config=config,
        max_per_pass=int(os.environ.get("REENGAGEMENT_MAX_PER_PASS", "1000")),
    )
    token = os.environ.get("REENGAGEMENT_TASK_TOKEN")
    return create_app(
        settings,
        store=store,
        handler=orchestrator,
        clock=clock,
        applications=orchestrator,
        reengagement=reengagement,
        reengagement_token=SecretStr(token) if token else None,
        crm_router=build_crm_router_from_env(repository, clock),
    )
