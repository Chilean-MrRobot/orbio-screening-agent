"""Chat web (spec 019): mismo orquestador que WhatsApp, por HTTP y sin Meta."""

from __future__ import annotations

import random

import httpx
import phonenumbers
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from screening.adapters.memory import InMemoryCandidateRepository
from screening.api.app import whatsapp_settings_or_none
from screening.api.web_chat import ChatView, build_web_chat_router, generate_test_phone
from screening.channels import CandidateBusyError, WebChatChannel
from screening.clock import FixedClock
from screening.config import AppConfig
from screening.conversation import ConversationOrchestrator
from screening.crm.chat_client import ChatClient
from screening.crm.client import CrmApiError
from screening.domain import ChannelName, Country, MessageRole
from screening.ports import ExtractionOutcome
from tests.unit.conversation.conftest import ScriptedAgent

START = {"full_name": "Lucía Pérez", "country": "ES", "phone": "+34600111222"}


@pytest.fixture
def agent() -> ScriptedAgent:
    return ScriptedAgent()


@pytest.fixture
def orchestrator(
    repository: InMemoryCandidateRepository,
    agent: ScriptedAgent,
    clock: FixedClock,
    app_config: AppConfig,
) -> ConversationOrchestrator:
    return ConversationOrchestrator(
        repository=repository,
        agent=agent,
        channel=WebChatChannel(),
        clock=clock,
        config=app_config,
    )


@pytest.fixture
def client(
    orchestrator: ConversationOrchestrator,
    repository: InMemoryCandidateRepository,
    clock: FixedClock,
) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_web_chat_router(
            starter=orchestrator, handler=orchestrator, repository=repository, clock=clock
        )
    )
    return TestClient(app)


def test_start_creates_the_application_and_returns_the_first_message(
    client: TestClient, repository: InMemoryCandidateRepository
) -> None:
    response = client.post("/chat/start", json=START)

    assert response.status_code == 200
    view = ChatView.model_validate(response.json())
    assert view.status == "contacted"
    assert [m.role for m in view.messages] == ["agent"]
    assert view.messages[0].text.startswith("¡Hola Lucía! Soy Sofía")
    stored = repository.get(view.candidate_id)
    assert stored is not None
    assert stored.application.source == "web"
    assert stored.messages[0].channel == ChannelName.WEB


def test_message_goes_through_the_orchestrator(
    client: TestClient, agent: ScriptedAgent, repository: InMemoryCandidateRepository
) -> None:
    view = ChatView.model_validate(client.post("/chat/start", json=START).json())
    agent.queue(ExtractionOutcome(provided_fields=["consent"], updates={"consent": True}))

    response = client.post(f"/chat/{view.candidate_id}/messages", json={"text": "sí, acepto"})

    assert response.status_code == 200
    updated = ChatView.model_validate(response.json())
    assert [m.role for m in updated.messages] == ["agent", "candidate", "agent"]
    assert updated.messages[1].text == "sí, acepto"
    assert updated.stage == "knockout"
    stored = repository.get(view.candidate_id)
    assert stored is not None and stored.data.consent is True
    candidate_message = next(m for m in stored.messages if m.role == MessageRole.CANDIDATE)
    assert candidate_message.channel == ChannelName.WEB


def test_get_returns_the_conversation_and_unknown_id_is_404(client: TestClient) -> None:
    view = ChatView.model_validate(client.post("/chat/start", json=START).json())

    assert client.get(f"/chat/{view.candidate_id}").json()["candidate_id"] == view.candidate_id
    assert client.get("/chat/nope").status_code == 404
    assert client.post("/chat/nope/messages", json={"text": "hola"}).status_code == 404


def test_without_phone_a_valid_test_number_is_generated(client: TestClient) -> None:
    view = ChatView.model_validate(
        client.post("/chat/start", json={"full_name": "Juan López", "country": "MX"}).json()
    )

    assert view.phone is not None and view.phone.startswith("+52")


def test_invalid_phone_is_422(client: TestClient) -> None:
    response = client.post("/chat/start", json=START | {"phone": "123"})

    assert response.status_code == 422


@pytest.mark.parametrize("body", [{"text": ""}, {"text": "x" * 1001}])
def test_message_length_is_validated(client: TestClient, body: dict) -> None:
    view = ChatView.model_validate(client.post("/chat/start", json=START).json())

    assert client.post(f"/chat/{view.candidate_id}/messages", json=body).status_code == 422


def test_closed_conversation_is_not_processed_again(
    client: TestClient, agent: ScriptedAgent
) -> None:
    view = ChatView.model_validate(client.post("/chat/start", json=START).json())
    agent.queue(ExtractionOutcome(provided_fields=["consent"], updates={"consent": False}))
    closed = ChatView.model_validate(
        client.post(f"/chat/{view.candidate_id}/messages", json={"text": "no"}).json()
    )
    assert closed.done and closed.status == "disqualified"
    calls = len(agent.extract_calls)

    again = client.post(f"/chat/{view.candidate_id}/messages", json={"text": "¿hola?"})

    assert again.status_code == 200
    assert len(agent.extract_calls) == calls


def test_busy_candidate_is_409(
    client: TestClient, orchestrator: ConversationOrchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = ChatView.model_validate(client.post("/chat/start", json=START).json())

    def busy(batch: object) -> None:
        raise CandidateBusyError("+34600111222")

    monkeypatch.setattr(orchestrator, "handle", busy)

    assert (
        client.post(f"/chat/{view.candidate_id}/messages", json={"text": "hola"}).status_code == 409
    )


@pytest.mark.parametrize("country", list(Country))
def test_generated_phone_is_valid_for_the_country(country: Country) -> None:
    phone = generate_test_phone(country, random.Random(7))

    parsed = phonenumbers.parse(phone)
    assert phonenumbers.is_valid_number(parsed)
    assert phonenumbers.region_code_for_number(parsed) == country.value


def test_whatsapp_is_optional() -> None:
    assert whatsapp_settings_or_none({}) is None
    settings = whatsapp_settings_or_none({"WHATSAPP_VERIFY_TOKEN": "v", "WHATSAPP_APP_SECRET": "s"})
    assert settings is not None
    with pytest.raises(ValueError, match="WHATSAPP_APP_SECRET"):
        whatsapp_settings_or_none({"WHATSAPP_VERIFY_TOKEN": "v"})


def test_app_without_whatsapp_mounts_chat_but_not_webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from screening.api import create_app_from_env
    from tests.unit.conversation.conftest import ScriptedAgent as Agent

    for name in ("WHATSAPP_VERIFY_TOKEN", "WHATSAPP_APP_SECRET", "WHATSAPP_ACCESS_TOKEN"):
        monkeypatch.setenv(name, "")
    monkeypatch.delenv("SCREENING_REPOSITORY", raising=False)

    paths = set(TestClient(create_app_from_env(agent=Agent())).get("/openapi.json").json()["paths"])

    assert "/chat/start" in paths
    assert "/webhooks/whatsapp" not in paths


def test_chat_client_round_trip() -> None:
    view = {
        "candidate_id": "c1",
        "phone": "+34600111222",
        "status": "contacted",
        "stage": "welcome",
        "done": False,
        "messages": [{"role": "agent", "text": "¡Hola!", "at": "2026-10-22T10:00:00Z"}],
    }
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path.endswith("/boom/messages"):
            return httpx.Response(409, json={"detail": "Sofía está respondiendo, espera"})
        return httpx.Response(200, json=view)

    client = ChatClient("http://api", transport=httpx.MockTransport(handler))

    assert client.start(full_name="Lucía", country=Country.ES).candidate_id == "c1"
    assert client.get("c1").messages[0].text == "¡Hola!"
    assert client.send("c1", "hola").stage == "welcome"
    with pytest.raises(CrmApiError) as error:
        client.send("boom", "hola")
    assert error.value.status_code == 409
    assert seen[:3] == [
        ("POST", "/chat/start"),
        ("GET", "/chat/c1"),
        ("POST", "/chat/c1/messages"),
    ]
