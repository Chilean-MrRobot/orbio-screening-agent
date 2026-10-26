"""Armado de las dependencias reales según el entorno (spec 008)."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from screening.adapters.fake_knowledge import FakeKnowledgeBase
from screening.adapters.memory import InMemoryCandidateRepository, InMemoryLeaseStore
from screening.channels import InboundMessageStore, InMemoryInboundMessageStore
from screening.clock import Clock
from screening.config import AppConfig
from screening.domain import Candidate, Message
from screening.knowledge.base import EmbeddingKnowledgeBase
from screening.knowledge.chunking import load_chunks
from screening.knowledge.embeddings import OpenAIEmbedder
from screening.knowledge.index import DEFAULT_INDEX_PATH, KnowledgeIndexError, load_index
from screening.ports import (
    CandidateRepository,
    ExtractionOutcome,
    KnowledgeBase,
    LeaseStore,
    ReplyOutcome,
    ReplyRequest,
    ScreeningAgent,
)

if TYPE_CHECKING:
    from google.cloud import firestore

    from screening.summary import SummaryService

logger = logging.getLogger(__name__)

SUMMARY_TIMEOUT_SECONDS = 30.0


class UnconfiguredAgent:
    """Agente de reemplazo sin `OPENAI_API_KEY`: falla y el orquestador pide disculpas."""

    def extract(
        self, candidate: Candidate, new_messages: list[Message], expected_fields: list[str]
    ) -> ExtractionOutcome:
        raise RuntimeError("no hay un ScreeningAgent configurado (falta OPENAI_API_KEY, spec 009)")

    def reply(self, request: ReplyRequest) -> ReplyOutcome:
        raise RuntimeError("no hay un ScreeningAgent configurado (falta OPENAI_API_KEY, spec 009)")


def load_agent(
    config: AppConfig, clock: Clock, env: Mapping[str, str] = os.environ
) -> ScreeningAgent:
    """Agente ADK (spec 009) con la FAQ (spec 010). Sin `OPENAI_API_KEY`, el de reemplazo."""
    if not env.get("OPENAI_API_KEY"):
        logger.warning("Sin OPENAI_API_KEY: las conversaciones responderán con una disculpa")
        return UnconfiguredAgent()

    from screening.agent import AdkScreeningAgent

    return AdkScreeningAgent.from_env(config, load_knowledge(config), clock=clock)


def load_summarizer(
    config: AppConfig, env: Mapping[str, str] = os.environ
) -> SummaryService | None:
    """Resumen post-conversación (spec 012). Sin `OPENAI_API_KEY`, las fichas quedan sin él."""
    api_key = env.get("OPENAI_API_KEY")
    if not api_key:
        logger.warning("Sin OPENAI_API_KEY: las fichas cerradas no tendrán resumen")
        return None

    import openai

    from screening.adapters.openai_llm import OpenAILLMClient
    from screening.summary import SummaryService

    sdk = openai.OpenAI(api_key=api_key, max_retries=0, timeout=SUMMARY_TIMEOUT_SECONDS)
    llm = OpenAILLMClient.from_env(client=sdk, reasoning_effort=config.llm.reasoning_effort)
    return SummaryService(llm, config)


def load_knowledge(config: AppConfig, index_path: Path | str = DEFAULT_INDEX_PATH) -> KnowledgeBase:
    """La FAQ por embeddings; sin índice construido, coincidencia de palabras sobre la FAQ."""
    try:
        index = load_index(index_path)
        embedder = OpenAIEmbedder.from_env(config.llm.embedding_model)
        return EmbeddingKnowledgeBase.from_index(index, embedder)
    except KnowledgeIndexError as e:
        logger.warning("RAG degradado a coincidencia de palabras: %s", e)
        return FakeKnowledgeBase((chunk.source, chunk.text) for chunk in load_chunks())


def build_storage(
    env: Mapping[str, str] = os.environ,
) -> tuple[CandidateRepository, InboundMessageStore]:
    """`SCREENING_REPOSITORY=firestore` usa Firestore; por defecto, memoria."""
    backend = (env.get("SCREENING_REPOSITORY") or "memory").lower()
    if backend == "memory":
        return InMemoryCandidateRepository(), InMemoryInboundMessageStore()
    if backend != "firestore":
        raise ValueError(f"SCREENING_REPOSITORY desconocido: {backend!r} (memory o firestore)")

    from screening.adapters.firestore import (
        FirestoreCandidateRepository,
        FirestoreInboundMessageStore,
    )

    client = _firestore_client(env)
    return FirestoreCandidateRepository(client), FirestoreInboundMessageStore(client)


def build_lease_store(env: Mapping[str, str] = os.environ) -> LeaseStore:
    """Lease por candidato (spec 015), en el mismo backend que `build_storage`."""
    backend = (env.get("SCREENING_REPOSITORY") or "memory").lower()
    if backend == "memory":
        return InMemoryLeaseStore()
    if backend != "firestore":
        raise ValueError(f"SCREENING_REPOSITORY desconocido: {backend!r} (memory o firestore)")

    from screening.adapters.firestore import FirestoreLeaseStore

    return FirestoreLeaseStore(_firestore_client(env))


def _firestore_client(env: Mapping[str, str]) -> firestore.Client:
    from google.cloud import firestore

    options = {"project": env.get("FIRESTORE_PROJECT"), "database": env.get("FIRESTORE_DATABASE")}
    return firestore.Client(**{k: v for k, v in options.items() if v})
