from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from screening.adapters.fake_knowledge import FakeKnowledgeBase
from screening.adapters.memory import InMemoryCandidateRepository
from screening.agent import AdkScreeningAgent
from screening.api import create_app_from_env
from screening.api.wiring import (
    UnconfiguredAgent,
    build_storage,
    load_agent,
    load_knowledge,
    load_summarizer,
)
from screening.channels import InMemoryInboundMessageStore
from screening.clock import FixedClock
from screening.config import AppConfig
from screening.summary import SummaryService
from tests.unit.conversation.conftest import APPLICATION, ScriptedAgent


def test_memory_storage_is_the_default() -> None:
    repository, store = build_storage({})

    assert isinstance(repository, InMemoryCandidateRepository)
    assert isinstance(store, InMemoryInboundMessageStore)


def test_unknown_storage_backend_fails() -> None:
    with pytest.raises(ValueError, match="SCREENING_REPOSITORY"):
        build_storage({"SCREENING_REPOSITORY": "postgres"})


def test_unconfigured_agent_raises() -> None:
    with pytest.raises(RuntimeError, match="009"):
        UnconfiguredAgent().reply(None)  # type: ignore[arg-type]


def test_load_agent_without_api_key_is_unconfigured(
    app_config: AppConfig, clock: FixedClock
) -> None:
    assert isinstance(load_agent(app_config, clock, env={}), UnconfiguredAgent)


def test_load_agent_with_api_key_builds_the_adk_agent(
    app_config: AppConfig, clock: FixedClock, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "screening.api.wiring.load_knowledge",
        lambda config: load_knowledge(config, tmp_path / "missing.json"),
    )

    agent = load_agent(app_config, clock, env={"OPENAI_API_KEY": "sk-test"})

    assert isinstance(agent, AdkScreeningAgent)
    assert isinstance(agent.knowledge, FakeKnowledgeBase)


def test_load_summarizer_needs_the_api_key(app_config: AppConfig) -> None:
    assert load_summarizer(app_config, env={}) is None

    summarizer = load_summarizer(app_config, env={"OPENAI_API_KEY": "sk-test"})

    assert isinstance(summarizer, SummaryService)
    assert summarizer.model == app_config.llm.conversation_model


def test_knowledge_without_index_falls_back_to_the_faq_words(
    app_config: AppConfig, tmp_path: Path
) -> None:
    knowledge = load_knowledge(app_config, tmp_path / "missing.json")

    assert isinstance(knowledge, FakeKnowledgeBase)
    assert knowledge.search("¿Cuándo pagan?")  # la FAQ real habla del pago


def test_app_from_env_wires_the_orchestrator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "v")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "s")
    monkeypatch.delenv("WHATSAPP_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("SCREENING_REPOSITORY", raising=False)

    app = create_app_from_env(agent=ScriptedAgent())
    response = TestClient(app).post("/applications", json=APPLICATION)

    assert response.status_code == 201
    assert response.json()["contacted"] is True  # canal de consola
