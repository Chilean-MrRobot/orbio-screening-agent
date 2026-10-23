from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from screening.adapters.memory import InMemoryCandidateRepository
from screening.api import WhatsAppSettings, create_app
from screening.channels import InMemoryInboundMessageStore
from screening.conversation import ConversationOrchestrator
from screening.domain import CandidateStatus
from screening.intake import IntakeOutcome
from tests.unit.conversation.conftest import APPLICATION, PHONE, RecordingChannel


def test_new_application_gets_first_contact(
    orchestrator: ConversationOrchestrator,
    channel: RecordingChannel,
    repository: InMemoryCandidateRepository,
) -> None:
    result = orchestrator.start_application(dict(APPLICATION))

    assert result.outcome == IntakeOutcome.CREATED
    assert result.contacted
    assert result.status == CandidateStatus.CONTACTED
    assert channel.sent[0].to == PHONE
    assert "Grupo Sazón" in channel.sent[0].text
    candidate = repository.get_by_phone(PHONE)
    assert candidate is not None
    assert candidate.status == CandidateStatus.CONTACTED
    assert candidate.messages[0].text == channel.sent[0].text
    sent = [e.detail for e in candidate.timeline if e.type == "message_sent"]
    assert sent[0]["template"] == "first_contact"


def test_duplicate_application_is_not_contacted_again(
    orchestrator: ConversationOrchestrator, channel: RecordingChannel
) -> None:
    orchestrator.start_application(dict(APPLICATION))
    result = orchestrator.start_application({**APPLICATION, "application_id": "app-002"})

    assert result.outcome == IntakeOutcome.DUPLICATE
    assert not result.contacted
    assert len(channel.sent) == 1


def test_invalid_phone_is_not_contacted(
    orchestrator: ConversationOrchestrator, channel: RecordingChannel
) -> None:
    result = orchestrator.start_application({**APPLICATION, "phone": "123"})

    assert result.outcome == IntakeOutcome.INVALID_DATA
    assert result.status == CandidateStatus.INVALID_DATA
    assert not result.contacted
    assert channel.sent == []


def test_failed_first_contact_leaves_candidate_new(
    orchestrator: ConversationOrchestrator,
    channel: RecordingChannel,
    repository: InMemoryCandidateRepository,
) -> None:
    channel.fail = True
    result = orchestrator.start_application(dict(APPLICATION))

    assert not result.contacted
    candidate = repository.get_by_phone(PHONE)
    assert candidate is not None
    assert candidate.status == CandidateStatus.NEW
    assert any(e.type == "message_failed" for e in candidate.timeline)


@pytest.fixture
def client(orchestrator: ConversationOrchestrator, clock) -> TestClient:
    settings = WhatsAppSettings(verify_token=SecretStr("v"), app_secret=SecretStr("s"))
    app = create_app(
        settings,
        store=InMemoryInboundMessageStore(),
        handler=orchestrator,
        clock=clock,
        applications=orchestrator,
    )
    return TestClient(app)


def test_applications_endpoint_creates_and_contacts(
    client: TestClient, channel: RecordingChannel
) -> None:
    response = client.post("/applications", json=APPLICATION)

    assert response.status_code == 201
    body = response.json()
    assert body["outcome"] == "created"
    assert body["contacted"] is True
    assert body["candidate_id"]
    assert len(channel.sent) == 1


def test_applications_endpoint_status_codes(client: TestClient) -> None:
    assert client.post("/applications", json=APPLICATION).status_code == 201
    assert client.post("/applications", json=APPLICATION).status_code == 200
    assert client.post("/applications", json={"source": "x"}).status_code == 422
    assert client.post("/applications", json=[1, 2]).status_code == 422
    assert client.post("/applications", content=b"no json").status_code == 422


def test_applications_endpoint_absent_without_starter(clock) -> None:
    settings = WhatsAppSettings(verify_token=SecretStr("v"), app_secret=SecretStr("s"))
    app = create_app(
        settings,
        store=InMemoryInboundMessageStore(),
        handler=RecordingChannel(),  # type: ignore[arg-type]
        clock=clock,
    )

    assert TestClient(app).post("/applications", json=APPLICATION).status_code == 404
