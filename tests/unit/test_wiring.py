from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from screening.adapters.memory import InMemoryCandidateRepository
from screening.api import create_app_from_env
from screening.api.wiring import UnconfiguredAgent, build_storage
from screening.channels import InMemoryInboundMessageStore
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


def test_app_from_env_wires_the_orchestrator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "v")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "s")
    monkeypatch.delenv("WHATSAPP_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("SCREENING_REPOSITORY", raising=False)

    app = create_app_from_env(agent=ScriptedAgent())
    response = TestClient(app).post("/applications", json=APPLICATION)

    assert response.status_code == 201
    assert response.json()["contacted"] is True  # canal de consola
