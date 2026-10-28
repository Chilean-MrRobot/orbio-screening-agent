"""Llamada en tiempo real (spec 022): endpoint compatible con OpenAI e inicio de llamada."""

from __future__ import annotations

import json
import threading
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from screening.adapters.elevenlabs import VoiceError, speakable
from screening.adapters.memory import InMemoryCandidateRepository
from screening.api.voice_call import (
    WAIT_TEMPLATES,
    CallSession,
    CallSettings,
    build_voice_call_router,
    candidate_id_from,
    is_filler,
    user_text_from,
    wait_template,
)
from screening.api.web_chat import WebChatService
from screening.channels import CandidateBusyError, InboundBatch, WebChatChannel
from screening.clock import FixedClock
from screening.config import AppConfig
from screening.conversation import ConversationOrchestrator
from screening.crm.call_client import CallClient
from screening.crm.client import CrmApiError
from screening.domain import ChannelName, MessageRole
from screening.ports import ExtractionOutcome, ReplyOutcome, ReplyRequest
from tests.unit.conversation.conftest import USAGE, ScriptedAgent

START = {"full_name": "Lucía Pérez", "country": "ES", "phone": "+34600111222"}
SECRET = "llm-secret"
AUTH = {"Authorization": f"Bearer {SECRET}"}
LLM_PATH = "/voice/llm/v1/chat/completions"
END_CALL_TOOL = {
    "type": "function",
    "function": {"name": "end_call", "description": "Ends the call", "parameters": {}},
}


class FakeTokens:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail = False

    def conversation_token(self, agent_id: str) -> str:
        self.calls.append(agent_id)
        if self.fail:
            raise VoiceError("ElevenLabs respondió 500")
        return f"token-{len(self.calls)}"


class SlowHandler:
    """Envuelve el orquestador para simular un turno lento del LLM."""

    def __init__(self, inner: ConversationOrchestrator, seconds: float) -> None:
        self.inner = inner
        self.seconds = seconds

    def handle(self, batch: InboundBatch) -> None:
        time.sleep(self.seconds)
        self.inner.handle(batch)


class BusyHandler:
    def __init__(self) -> None:
        self.calls = 0

    def handle(self, batch: InboundBatch) -> None:
        self.calls += 1
        raise CandidateBusyError(batch.sender)


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
def tokens() -> FakeTokens:
    return FakeTokens()


def settings(**overrides: object) -> CallSettings:
    values: dict[str, object] = {
        "agent_id": "agent-1",
        "llm_secret": SecretStr(SECRET),
        "wait_after_seconds": 0,
        "wait_repeat_seconds": 0,
        "heartbeat_seconds": 0,
        "busy_wait_seconds": 0,
    }
    values.update(overrides)
    return CallSettings.model_validate(values)


def make_client(
    orchestrator: ConversationOrchestrator,
    repository: InMemoryCandidateRepository,
    clock: FixedClock,
    app_config: AppConfig,
    tokens: FakeTokens | None,
    call_settings: CallSettings,
    handler: object | None = None,
) -> TestClient:
    chat = WebChatService(
        starter=orchestrator,
        handler=handler or orchestrator,  # type: ignore[arg-type]
        repository=repository,
        clock=clock,
    )
    app = FastAPI()
    app.include_router(
        build_voice_call_router(
            chat=chat,
            tokens=tokens,
            settings=call_settings,
            config=app_config,
            sleep=lambda _: None,
        )
    )
    return TestClient(app)


@pytest.fixture
def client(
    orchestrator: ConversationOrchestrator,
    repository: InMemoryCandidateRepository,
    clock: FixedClock,
    app_config: AppConfig,
    tokens: FakeTokens,
) -> TestClient:
    return make_client(orchestrator, repository, clock, app_config, tokens, settings())


def start(client: TestClient) -> CallSession:
    response = client.post("/voice/call/start", json=START)
    assert response.status_code == 200, response.text
    return CallSession.model_validate(response.json())


def completion_body(candidate_id: str, *user: str, stream: bool = False, tools=None) -> dict:
    messages = [
        {"role": "system", "content": f"Eres Sofía.\ncandidate_id: {candidate_id}\n"},
        {"role": "assistant", "content": "¡Hola Lucía! Soy Sofía."},
        *({"role": "user", "content": text} for text in user),
    ]
    body: dict = {"model": "orbio-orchestrator", "messages": messages, "stream": stream}
    if tools is not None:
        body["tools"] = tools
    return body


def sse_events(response: httpx.Response) -> list[dict]:
    lines = [line for line in response.text.splitlines() if line.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"
    return [json.loads(line.removeprefix("data: ")) for line in lines[:-1]]


def streamed_text(events: list[dict]) -> str:
    return "".join(e["choices"][0]["delta"].get("content") or "" for e in events)


# --- Inicio de llamada ---------------------------------------------------------------


def test_start_creates_the_application_and_returns_a_token(
    client: TestClient, tokens: FakeTokens, repository: InMemoryCandidateRepository
) -> None:
    session = start(client)

    assert session.conversation_token == "token-1"
    assert tokens.calls == ["agent-1"]
    assert session.dynamic_variables == {
        "candidate_id": session.chat.candidate_id,
        "first_name": "Lucía",
    }
    assert session.language == "es"
    assert session.chat.messages[0].text.startswith("¡Hola Lucía! Soy Sofía")
    stored = repository.get(session.chat.candidate_id)
    assert stored is not None and stored.application.source == "web"


def test_token_for_an_existing_call(client: TestClient, tokens: FakeTokens) -> None:
    session = start(client)

    response = client.post(f"/voice/call/{session.chat.candidate_id}/token")

    assert response.status_code == 200
    assert CallSession.model_validate(response.json()).conversation_token == "token-2"
    assert client.post("/voice/call/nope/token").status_code == 404


def test_token_failure_is_502(client: TestClient, tokens: FakeTokens) -> None:
    tokens.fail = True

    response = client.post("/voice/call/start", json=START)

    assert response.status_code == 502


def test_call_disabled_without_configuration(
    orchestrator: ConversationOrchestrator,
    repository: InMemoryCandidateRepository,
    clock: FixedClock,
    app_config: AppConfig,
    tokens: FakeTokens,
) -> None:
    cases = [
        (None, settings()),
        (tokens, settings(agent_id=None)),
        (tokens, settings(llm_secret=None)),
    ]
    for fake, call_settings in cases:
        client = make_client(orchestrator, repository, clock, app_config, fake, call_settings)

        assert client.get("/voice/call/status").json() == {"enabled": False}
        assert client.post("/voice/call/start", json=START).status_code == 503

    enabled = make_client(orchestrator, repository, clock, app_config, tokens, settings())
    assert enabled.get("/voice/call/status").json() == {"enabled": True}


def test_settings_from_env() -> None:
    empty = CallSettings.from_env({})
    assert empty.agent_id is None and empty.llm_secret is None
    loaded = CallSettings.from_env(
        {
            "ELEVENLABS_AGENT_ID": "agent-9",
            "VOICE_LLM_SECRET": "s3cret",
            "VOICE_WAIT_AFTER_SECONDS": "2.5",
            "VOICE_BUSY_WAIT_SECONDS": "4",
        }
    )
    assert loaded.agent_id == "agent-9"
    assert loaded.llm_secret is not None and loaded.llm_secret.get_secret_value() == "s3cret"
    assert loaded.wait_after_seconds == 2.5 and loaded.busy_wait_seconds == 4


# --- Endpoint compatible con OpenAI ---------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": SECRET}],
    ids=["missing", "wrong", "no-bearer"],
)
def test_llm_endpoint_requires_the_secret(
    client: TestClient, agent: ScriptedAgent, headers: dict
) -> None:
    session = start(client)
    calls = len(agent.extract_calls)

    response = client.post(
        LLM_PATH, json=completion_body(session.chat.candidate_id, "hola"), headers=headers
    )

    assert response.status_code == 401
    assert len(agent.extract_calls) == calls


def test_llm_endpoint_disabled_without_secret(
    orchestrator: ConversationOrchestrator,
    repository: InMemoryCandidateRepository,
    clock: FixedClock,
    app_config: AppConfig,
    tokens: FakeTokens,
) -> None:
    client = make_client(
        orchestrator, repository, clock, app_config, tokens, settings(llm_secret=None)
    )

    assert client.post(LLM_PATH, json=completion_body("x", "hola"), headers=AUTH).status_code == 503


def test_json_completion_goes_through_the_orchestrator(
    client: TestClient, agent: ScriptedAgent, repository: InMemoryCandidateRepository
) -> None:
    session = start(client)
    agent.queue(ExtractionOutcome(provided_fields=["consent"], updates={"consent": True}))

    response = client.post(
        LLM_PATH, json=completion_body(session.chat.candidate_id, "Sí, acepto."), headers=AUTH
    )

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    message = body["choices"][0]["message"]
    assert message["role"] == "assistant"
    stored = repository.get(session.chat.candidate_id)
    assert stored is not None and stored.data.consent is True
    said = [m for m in stored.messages if m.role == MessageRole.CANDIDATE]
    assert [m.text for m in said] == ["Sí, acepto."]
    assert said[0].channel == ChannelName.WEB
    reply = stored.messages[-1]
    assert reply.role == MessageRole.AGENT
    assert message["content"] == speakable(reply.text)
    assert body["choices"][0]["finish_reason"] == "stop"


def test_streaming_completion_is_sse_with_only_the_new_reply(
    client: TestClient, agent: ScriptedAgent, repository: InMemoryCandidateRepository
) -> None:
    session = start(client)
    agent.queue(ExtractionOutcome(provided_fields=["consent"], updates={"consent": True}))

    response = client.post(
        LLM_PATH,
        json=completion_body(session.chat.candidate_id, "Sí, acepto.", stream=True),
        headers=AUTH,
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = sse_events(response)
    assert all(e["object"] == "chat.completion.chunk" for e in events)
    assert events[0]["choices"][0]["delta"]["role"] == "assistant"
    assert events[-1]["choices"][0]["finish_reason"] == "stop"
    stored = repository.get(session.chat.candidate_id)
    assert stored is not None
    assert streamed_text(events).strip() == speakable(stored.messages[-1].text)
    assert "Hola Lucía" not in streamed_text(events)  # el saludo no se repite


def test_trailing_user_messages_are_joined(
    client: TestClient, repository: InMemoryCandidateRepository
) -> None:
    session = start(client)

    client.post(
        LLM_PATH,
        json=completion_body(session.chat.candidate_id, "Sí,", "acepto."),
        headers=AUTH,
    )

    stored = repository.get(session.chat.candidate_id)
    assert stored is not None
    said = [m.text for m in stored.messages if m.role == MessageRole.CANDIDATE]
    assert said == ["Sí, acepto."]


def test_slow_turn_sends_the_wait_phrase_first(
    orchestrator: ConversationOrchestrator,
    repository: InMemoryCandidateRepository,
    clock: FixedClock,
    app_config: AppConfig,
    tokens: FakeTokens,
    agent: ScriptedAgent,
) -> None:
    client = make_client(
        orchestrator,
        repository,
        clock,
        app_config,
        tokens,
        settings(wait_after_seconds=0.05),
        handler=SlowHandler(orchestrator, 0.4),
    )
    session = start(client)
    agent.queue(ExtractionOutcome(provided_fields=["consent"], updates={"consent": True}))

    response = client.post(
        LLM_PATH,
        json=completion_body(session.chat.candidate_id, "sí", stream=True),
        headers=AUTH,
    )

    events = sse_events(response)
    contents = [e["choices"][0]["delta"].get("content") for e in events]
    contents = [c for c in contents if c]
    wait_es = app_config.templates["voice_wait"]["es"]
    assert contents[0].strip() == wait_es
    stored = repository.get(session.chat.candidate_id)
    assert stored is not None
    assert wait_es not in [m.text for m in stored.messages]  # no se guarda en la ficha
    assert speakable(stored.messages[-1].text) in streamed_text(events)


def test_fast_turn_has_no_wait_phrase(
    orchestrator: ConversationOrchestrator,
    repository: InMemoryCandidateRepository,
    clock: FixedClock,
    app_config: AppConfig,
    tokens: FakeTokens,
) -> None:
    client = make_client(
        orchestrator, repository, clock, app_config, tokens, settings(wait_after_seconds=5)
    )
    session = start(client)

    response = client.post(
        LLM_PATH,
        json=completion_body(session.chat.candidate_id, "sí", stream=True),
        headers=AUTH,
    )

    assert app_config.templates["voice_wait"]["es"] not in streamed_text(sse_events(response))


def test_closed_conversation_ends_the_call(client: TestClient, agent: ScriptedAgent) -> None:
    session = start(client)
    agent.queue(ExtractionOutcome(provided_fields=["consent"], updates={"consent": False}))

    response = client.post(
        LLM_PATH,
        json=completion_body(session.chat.candidate_id, "no", stream=True, tools=[END_CALL_TOOL]),
        headers=AUTH,
    )

    events = sse_events(response)
    calls = [call for e in events for call in e["choices"][0]["delta"].get("tool_calls") or []]
    assert [c["function"]["name"] for c in calls] == ["end_call"]
    assert events[-1]["choices"][0]["finish_reason"] == "tool_calls"
    assert streamed_text(events).strip()  # se despide antes de colgar


def test_request_after_the_goodbye_only_hangs_up(
    client: TestClient, agent: ScriptedAgent, repository: InMemoryCandidateRepository
) -> None:
    # Spec 023: ElevenLabs volvía a pedir con la transcripción final y el cierre salía 3 veces.
    session = start(client)
    agent.queue(ExtractionOutcome(provided_fields=["consent"], updates={"consent": False}))
    tools = [END_CALL_TOOL]
    client.post(
        LLM_PATH,
        json=completion_body(session.chat.candidate_id, "no", stream=True, tools=tools),
        headers=AUTH,
    )
    stored = repository.get(session.chat.candidate_id)
    assert stored is not None
    messages_before = len(stored.messages)

    response = client.post(
        LLM_PATH,
        json=completion_body(session.chat.candidate_id, "no, perdón, sí", stream=True, tools=tools),
        headers=AUTH,
    )

    events = sse_events(response)
    calls = [call for e in events for call in e["choices"][0]["delta"].get("tool_calls") or []]
    assert [c["function"]["name"] for c in calls] == ["end_call"]
    assert streamed_text(events).strip() == ""  # no repite el cierre
    stored = repository.get(session.chat.candidate_id)
    assert stored is not None and len(stored.messages) == messages_before


@pytest.mark.parametrize("text", ["Eh.", "Eeeh…", "Mmm, este", "ehm"])
def test_filler_only_turn_is_not_processed(
    client: TestClient,
    agent: ScriptedAgent,
    repository: InMemoryCandidateRepository,
    app_config: AppConfig,
    text: str,
) -> None:
    session = start(client)

    body = client.post(
        LLM_PATH, json=completion_body(session.chat.candidate_id, text), headers=AUTH
    ).json()

    assert body["choices"][0]["message"]["content"] == app_config.templates["voice_listening"]["es"]
    stored = repository.get(session.chat.candidate_id)
    assert stored is not None
    assert [m for m in stored.messages if m.role == MessageRole.CANDIDATE] == []
    assert agent.extract_calls == []


@pytest.mark.parametrize(
    ("text", "filler"),
    [("Eh.", True), ("Mmm…", True), ("Eh, sí", False), ("no", False), ("este lunes", False)],
)
def test_is_filler(text: str, filler: bool) -> None:
    assert is_filler(text) is filler


def test_closed_conversation_json_without_end_call_tool(
    client: TestClient, agent: ScriptedAgent
) -> None:
    session = start(client)
    agent.queue(ExtractionOutcome(provided_fields=["consent"], updates={"consent": False}))

    body = client.post(
        LLM_PATH, json=completion_body(session.chat.candidate_id, "no"), headers=AUTH
    ).json()

    assert body["choices"][0]["finish_reason"] == "stop"
    assert "tool_calls" not in body["choices"][0]["message"]


def test_busy_candidate_answers_with_the_busy_phrase(
    orchestrator: ConversationOrchestrator,
    repository: InMemoryCandidateRepository,
    clock: FixedClock,
    app_config: AppConfig,
    tokens: FakeTokens,
) -> None:
    busy = BusyHandler()
    client = make_client(
        orchestrator,
        repository,
        clock,
        app_config,
        tokens,
        settings(busy_wait_seconds=0.05),
        handler=busy,
    )
    session = start(client)

    body = client.post(
        LLM_PATH, json=completion_body(session.chat.candidate_id, "hola"), headers=AUTH
    ).json()

    expected = app_config.templates["voice_busy"]["es"].format(first_name="Lucía")
    assert body["choices"][0]["message"]["content"] == speakable(expected)
    assert busy.calls >= 1


def test_missing_candidate_id_is_400_and_unknown_is_404(client: TestClient) -> None:
    body = completion_body("x", "hola")
    body["messages"][0]["content"] = "Eres Sofía."

    assert client.post(LLM_PATH, json=body, headers=AUTH).status_code == 400
    assert (
        client.post(LLM_PATH, json=completion_body("nope", "hola"), headers=AUTH).status_code == 404
    )


def test_candidate_id_and_user_text_parsing() -> None:
    messages = [
        {"role": "system", "content": "Prompt\ncandidate_id: abc-123\nmás"},
        {"role": "assistant", "content": "hola"},
        {"role": "user", "content": "uno"},
        {"role": "assistant", "content": "dos"},
        {"role": "user", "content": " tres "},
        {"role": "user", "content": [{"type": "text", "text": "cuatro"}]},
    ]

    assert candidate_id_from(messages) == "abc-123"
    assert user_text_from(messages) == "tres cuatro"
    assert candidate_id_from([{"role": "user", "content": "hola"}]) is None
    assert user_text_from([{"role": "assistant", "content": "hola"}]) == ""


# --- Cliente de Streamlit -------------------------------------------------------------


def test_call_client_round_trip() -> None:
    view = {
        "candidate_id": "c1",
        "phone": "+34600111222",
        "status": "contacted",
        "stage": "welcome",
        "done": False,
        "messages": [{"role": "agent", "text": "¡Hola!", "at": "2026-10-22T10:00:00Z"}],
    }
    session = {
        "chat": view,
        "conversation_token": "tok",
        "dynamic_variables": {"candidate_id": "c1", "first_name": "Lucía"},
        "language": "es",
    }
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/voice/call/status":
            return httpx.Response(200, json={"enabled": True})
        if request.url.path == "/voice/call/boom/token":
            return httpx.Response(409, json={"detail": "la conversación ya terminó"})
        return httpx.Response(200, json=session)

    client = CallClient("http://api", transport=httpx.MockTransport(handler))

    assert client.enabled() is True
    assert client.start(full_name="Lucía", country="ES").conversation_token == "tok"
    assert client.token("c1").dynamic_variables["first_name"] == "Lucía"
    with pytest.raises(CrmApiError) as error:
        client.token("boom")
    assert error.value.status_code == 409
    assert seen[:3] == [
        ("GET", "/voice/call/status"),
        ("POST", "/voice/call/start"),
        ("POST", "/voice/call/c1/token"),
    ]


def test_widget_embeds_the_session_safely_and_pins_the_sdk() -> None:
    from screening.crm.call_widget import SDK_URL, widget_html

    session = CallSession.model_validate(
        {
            "chat": {
                "candidate_id": "c1",
                "phone": None,
                "status": "contacted",
                "stage": "welcome",
                "done": False,
                "messages": [],
            },
            "conversation_token": "tok</script><script>alert(1)",
            "dynamic_variables": {"candidate_id": "c1", "first_name": "Lucía"},
            "language": "es",
        }
    )

    html = widget_html(session)

    assert "@elevenlabs/client@1.28.0" in SDK_URL and SDK_URL in html
    assert "</script><script>alert(1)" not in html
    assert '"first_name": "Lucía"' in html
    assert "xi-api-key" not in html


def test_official_openai_client_as_elevenlabs_calls_it(
    client: TestClient, agent: ScriptedAgent, repository: InMemoryCandidateRepository
) -> None:
    """ElevenLabs usa el SDK de OpenAI (`AsyncOpenAI`) con `base_url` = la URL del agente y el
    secreto como api_key. Se reproduce con el SDK real sobre el TestClient."""
    from openai import OpenAI

    session = start(client)
    agent.queue(ExtractionOutcome(provided_fields=["consent"], updates={"consent": True}))
    llm = OpenAI(base_url=f"{client.base_url}/voice/llm/v1", api_key=SECRET, http_client=client)
    system = (
        "Eres Sofía, la asistente virtual de reclutamiento de Grupo Sazón.\n"
        f"candidate_id: {session.chat.candidate_id}\n"
    )

    stream = llm.chat.completions.create(
        model="orbio-screening-orchestrator",
        messages=[
            {"role": "system", "content": system},
            {"role": "assistant", "content": session.chat.messages[0].text},
            {"role": "user", "content": "Sí, acepto."},
        ],
        stream=True,
        temperature=0.5,
        max_tokens=5000,
        tools=[END_CALL_TOOL],  # type: ignore[list-item]
        extra_body={"user_id": "conv_123", "elevenlabs_extra_body": {}},
    )
    text = "".join(chunk.choices[0].delta.content or "" for chunk in stream)

    stored = repository.get(session.chat.candidate_id)
    assert stored is not None and stored.data.consent is True
    assert text.strip() == speakable(stored.messages[-1].text)


# --- Reintentos de ElevenLabs y turnos lentos -------------------------------------------

TWO_SENTENCES = "Perfecto, gracias. ¿Tienes carnet de conducir?"


class GatedAgent(ScriptedAgent):
    """Agente cuya extracción espera una señal: un LLM lento con el lease tomado."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = threading.Event()

    def extract(self, candidate, new_messages, expected_fields):  # type: ignore[no-untyped-def]
        assert self.gate.wait(timeout=10), "el test nunca liberó el agente"
        return super().extract(candidate, new_messages, expected_fields)

    def reply(self, request: ReplyRequest) -> ReplyOutcome:
        self.reply_calls.append(request)
        return ReplyOutcome(text=TWO_SENTENCES, usage=[USAGE])


@pytest.fixture
def gated() -> GatedAgent:
    return GatedAgent()


def gated_client(
    gated: GatedAgent,
    repository: InMemoryCandidateRepository,
    clock: FixedClock,
    app_config: AppConfig,
    tokens: FakeTokens,
    **overrides: object,
) -> TestClient:
    orchestrator = ConversationOrchestrator(
        repository=repository,
        agent=gated,
        channel=WebChatChannel(),
        clock=clock,
        config=app_config,
    )
    values: dict[str, object] = {"busy_wait_seconds": 5}
    values.update(overrides)
    chat = WebChatService(
        starter=orchestrator, handler=orchestrator, repository=repository, clock=clock
    )
    app = FastAPI()
    app.include_router(
        build_voice_call_router(
            chat=chat,
            tokens=tokens,
            settings=settings(**values),
            config=app_config,
            sleep=lambda _: time.sleep(0.01),
        )
    )
    return TestClient(app)


def candidate_texts(repository: InMemoryCandidateRepository, candidate_id: str) -> list[str]:
    stored = repository.get(candidate_id)
    assert stored is not None
    return [m.text for m in stored.messages if m.role == MessageRole.CANDIDATE]


def wait_until(condition, timeout: float = 5) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "la condición nunca se cumplió"
        time.sleep(0.01)


def in_background(fn):  # type: ignore[no-untyped-def]
    result: dict = {}
    thread = threading.Thread(target=lambda: result.setdefault("value", fn()))
    thread.start()
    return thread, result


def content(response: httpx.Response) -> str:
    return response.json()["choices"][0]["message"]["content"]


@pytest.mark.parametrize(
    "retry_tail",
    [
        [{"role": "user", "content": "Sí, acepto."}],
        [
            {"role": "user", "content": "Sí, acepto."},
            {"role": "assistant", "content": "Vale, déjame anotarlo."},
        ],
    ],
    ids=["same-user-text", "after-wait-phrase"],
)
def test_elevenlabs_retry_during_a_slow_turn_waits_for_the_real_reply(
    gated: GatedAgent,
    repository: InMemoryCandidateRepository,
    clock: FixedClock,
    app_config: AppConfig,
    tokens: FakeTokens,
    retry_tail: list[dict],
) -> None:
    """Producción: con el turno 1 en curso, ElevenLabs reintentó (cascada) y la API respondía
    vacío en 1–2 ms, así que ElevenLabs cortaba con `custom_llm_error`. Ahora el reintento
    espera el turno en curso, no lo procesa dos veces y devuelve la respuesta real."""
    client = gated_client(gated, repository, clock, app_config, tokens)
    session = start(client)
    cid = session.chat.candidate_id
    first, first_result = in_background(
        lambda: client.post(LLM_PATH, json=completion_body(cid, "Sí, acepto."), headers=AUTH)
    )
    wait_until(lambda: candidate_texts(repository, cid) == ["Sí, acepto."])
    retry_body = completion_body(cid)
    retry_body["messages"] = [
        {"role": "system", "content": f"candidate_id: {cid}"},
        {"role": "assistant", "content": session.chat.messages[0].text},
        *retry_tail,
    ]

    retry, retry_result = in_background(
        lambda: client.post(LLM_PATH, json=retry_body, headers=AUTH)
    )
    time.sleep(0.2)
    assert retry.is_alive(), "el reintento no debe responder mientras el turno sigue"
    gated.gate.set()
    first.join(5)
    retry.join(5)

    assert content(first_result["value"]) == TWO_SENTENCES
    assert content(retry_result["value"]) == TWO_SENTENCES
    assert candidate_texts(repository, cid) == ["Sí, acepto."]  # no se procesó dos veces
    assert len(gated.extract_calls) == 1


def test_retry_that_times_out_says_the_wait_phrase_instead_of_nothing(
    gated: GatedAgent,
    repository: InMemoryCandidateRepository,
    clock: FixedClock,
    app_config: AppConfig,
    tokens: FakeTokens,
) -> None:
    client = gated_client(gated, repository, clock, app_config, tokens, busy_wait_seconds=0.2)
    session = start(client)
    cid = session.chat.candidate_id
    first, _ = in_background(
        lambda: client.post(LLM_PATH, json=completion_body(cid, "hola"), headers=AUTH)
    )
    wait_until(lambda: candidate_texts(repository, cid) == ["hola"])

    response = client.post(LLM_PATH, json=completion_body(cid, "hola"), headers=AUTH)

    gated.gate.set()
    first.join(5)
    assert content(response) == speakable(app_config.templates["voice_wait"]["es"])


def test_request_without_new_text_never_returns_empty(
    client: TestClient, repository: InMemoryCandidateRepository
) -> None:
    """Sin texto nuevo y sin turno en curso (p. ej. silencio del candidato), Sofía repite su
    último mensaje en vez de devolver un stream vacío."""
    session = start(client)
    body = completion_body(session.chat.candidate_id, stream=True)
    body["messages"].append({"role": "assistant", "content": "¿Sigues ahí?"})

    response = client.post(LLM_PATH, json=body, headers=AUTH)

    text = streamed_text(sse_events(response)).strip()
    greeting = speakable(session.chat.messages[0].text)
    # "¡Hola Lucía!" ya se dijo (viene en el historial); el resto del saludo se repite.
    assert text and greeting.endswith(text) and not text.startswith("¡Hola")
    assert candidate_texts(repository, session.chat.candidate_id) == []


def test_genuine_repeat_of_the_same_answer_is_processed(
    client: TestClient, agent: ScriptedAgent, repository: InMemoryCandidateRepository
) -> None:
    session = start(client)
    cid = session.chat.candidate_id
    said = content(client.post(LLM_PATH, json=completion_body(cid, "sí"), headers=AUTH))
    body = completion_body(cid, "sí")
    body["messages"] += [{"role": "assistant", "content": said}, {"role": "user", "content": "sí"}]

    client.post(LLM_PATH, json=body, headers=AUTH)

    assert candidate_texts(repository, cid) == ["sí", "sí"]
    assert len(agent.extract_calls) == 2


def test_already_spoken_sentences_are_not_repeated(
    gated: GatedAgent,
    repository: InMemoryCandidateRepository,
    clock: FixedClock,
    app_config: AppConfig,
    tokens: FakeTokens,
) -> None:
    """Si el stream se cortó a mitad de la respuesta, el reintento dice solo lo que faltaba."""
    client = gated_client(gated, repository, clock, app_config, tokens)
    session = start(client)
    cid = session.chat.candidate_id
    gated.gate.set()
    assert content(client.post(LLM_PATH, json=completion_body(cid, "sí"), headers=AUTH)) == (
        TWO_SENTENCES
    )
    body = completion_body(cid, "sí")
    body["messages"].append({"role": "assistant", "content": "Vale. Perfecto, gracias."})

    retry = client.post(LLM_PATH, json=body, headers=AUTH)

    assert content(retry) == "¿Tienes carnet de conducir?"
    assert candidate_texts(repository, cid) == ["sí"]


def test_stream_keeps_alive_while_the_turn_runs(
    orchestrator: ConversationOrchestrator,
    repository: InMemoryCandidateRepository,
    clock: FixedClock,
    app_config: AppConfig,
    tokens: FakeTokens,
) -> None:
    """ElevenLabs pasa a la cascada si no recibe nada en `cascade_timeout_seconds`: mientras
    el turno corre se mandan chunks vacíos (heartbeat) y una segunda frase de espera."""
    client = make_client(
        orchestrator,
        repository,
        clock,
        app_config,
        tokens,
        settings(wait_after_seconds=0.05, wait_repeat_seconds=0.3, heartbeat_seconds=0.05),
        handler=SlowHandler(orchestrator, 0.6),
    )
    session = start(client)

    response = client.post(
        LLM_PATH,
        json=completion_body(session.chat.candidate_id, "sí", stream=True),
        headers=AUTH,
    )

    events = sse_events(response)
    contents = [e["choices"][0]["delta"].get("content") for e in events]
    spoken = [c.strip() for c in contents if c]
    assert spoken[0] == app_config.templates["voice_wait"]["es"]
    assert spoken[1] == app_config.templates["voice_wait_more"]["es"]
    assert len([c for c in contents[1:] if c == ""]) >= 3  # heartbeats
    stored = repository.get(session.chat.candidate_id)
    assert stored is not None
    # La respuesta va entera en un chunk: frase por frase, el TTS cortaba la entonación.
    assert spoken[2:] == [speakable(stored.messages[-1].text)]


def test_wait_phrase_alternates_by_turn(app_config: AppConfig) -> None:
    def history(answers: int) -> list[dict]:
        return completion_body("c1", *["sí"] * answers)["messages"]

    picked = [wait_template(history(n)) for n in range(1, 5)]

    assert picked == ["voice_wait", "voice_wait_2", "voice_wait_3", "voice_wait"]
    for key in WAIT_TEMPLATES:  # existen en los dos idiomas
        assert set(app_config.templates[key]) >= {"es", "en"}


def test_second_turn_uses_another_wait_phrase(
    orchestrator: ConversationOrchestrator,
    repository: InMemoryCandidateRepository,
    clock: FixedClock,
    app_config: AppConfig,
    tokens: FakeTokens,
) -> None:
    client = make_client(
        orchestrator,
        repository,
        clock,
        app_config,
        tokens,
        settings(wait_after_seconds=0.05),
        handler=SlowHandler(orchestrator, 0.3),
    )
    session = start(client)
    body = completion_body(session.chat.candidate_id, "sí", stream=True)
    body["messages"] += [
        {"role": "assistant", "content": "¿Tienes carnet?"},
        {"role": "user", "content": "sí, el B"},
    ]

    response = client.post(LLM_PATH, json=body, headers=AUTH)

    first = next(c for c in map(content_of, sse_events(response)) if c)
    assert first.strip() == app_config.templates["voice_wait_2"]["es"]


def content_of(event: dict) -> str | None:
    return event["choices"][0]["delta"].get("content")
