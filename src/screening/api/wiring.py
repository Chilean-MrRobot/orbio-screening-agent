"""Armado de las dependencias reales según el entorno (spec 008)."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping

from screening.adapters.memory import InMemoryCandidateRepository
from screening.channels import InboundMessageStore, InMemoryInboundMessageStore
from screening.config import AppConfig
from screening.domain import Candidate, Message
from screening.ports import (
    CandidateRepository,
    ExtractionOutcome,
    ReplyOutcome,
    ReplyRequest,
    ScreeningAgent,
)

logger = logging.getLogger(__name__)


class UnconfiguredAgent:
    """Agente de reemplazo hasta la spec 009: falla y el orquestador pide disculpas."""

    def extract(
        self, candidate: Candidate, new_messages: list[Message], expected_fields: list[str]
    ) -> ExtractionOutcome:
        raise RuntimeError("no hay un ScreeningAgent configurado (spec 009)")

    def reply(self, request: ReplyRequest) -> ReplyOutcome:
        raise RuntimeError("no hay un ScreeningAgent configurado (spec 009)")


def load_agent(config: AppConfig) -> ScreeningAgent:
    """Punto de inyección del agente real: la spec 009 lo reemplaza por el agente ADK."""
    logger.warning("Sin ScreeningAgent: las conversaciones responderán con una disculpa")
    return UnconfiguredAgent()


def build_storage(
    env: Mapping[str, str] = os.environ,
) -> tuple[CandidateRepository, InboundMessageStore]:
    """`SCREENING_REPOSITORY=firestore` usa Firestore; por defecto, memoria."""
    backend = (env.get("SCREENING_REPOSITORY") or "memory").lower()
    if backend == "memory":
        return InMemoryCandidateRepository(), InMemoryInboundMessageStore()
    if backend != "firestore":
        raise ValueError(f"SCREENING_REPOSITORY desconocido: {backend!r} (memory o firestore)")

    from google.cloud import firestore

    from screening.adapters.firestore import (
        FirestoreCandidateRepository,
        FirestoreInboundMessageStore,
    )

    options = {"project": env.get("FIRESTORE_PROJECT"), "database": env.get("FIRESTORE_DATABASE")}
    client = firestore.Client(**{k: v for k, v in options.items() if v})
    return FirestoreCandidateRepository(client), FirestoreInboundMessageStore(client)
