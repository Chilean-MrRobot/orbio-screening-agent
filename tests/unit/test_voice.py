"""Agente de voz (spec 020): endpoints /voice sobre el orquestador real, con voz falsa."""

from __future__ import annotations

import base64
import io
import wave

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from screening.adapters.elevenlabs import SpeechAudio, Transcript, VoiceError, speakable
from screening.adapters.memory import InMemoryCandidateRepository
from screening.api.voice import AudioLimits, VoiceTurn, build_voice_router, check_audio
from screening.api.web_chat import WebChatService
from screening.channels import WebChatChannel
from screening.clock import FixedClock
from screening.config import AppConfig
from screening.conversation import ConversationOrchestrator
from screening.crm.client import CrmApiError
from screening.crm.voice_client import VoiceClient
from screening.domain import ChannelName, Language, MessageRole
from screening.ports import ExtractionOutcome
from tests.unit.conversation.conftest import ScriptedAgent

START = {"full_name": "Lucía Pérez", "country": "ES", "phone": "+34600111222"}
LIMITS = AudioLimits(max_bytes=200_000, max_seconds=5)


def wav(seconds: float, rate: int = 8000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\x00\x00" * int(seconds * rate))
    return buffer.getvalue()


class FakeVoice:
    def __init__(self) -> None:
        self.transcripts: list[str] = []
        self.heard: list[bytes] = []
        self.spoken: list[tuple[str, Language]] = []
        self.fail_stt = False
        self.fail_tts = False

    def transcribe(self, audio: bytes, *, filename: str, content_type: str) -> Transcript:
        self.heard.append(audio)
        if self.fail_stt:
            raise VoiceError("ElevenLabs respondió 500")
        return Transcript(text=self.transcripts.pop(0) if self.transcripts else "", language="spa")

    def synthesize(self, text: str, *, language: Language) -> SpeechAudio:
        self.spoken.append((text, language))
        if self.fail_tts:
            raise VoiceError("ElevenLabs respondió 500")
        return SpeechAudio(data=f"audio:{text}".encode(), mime_type="audio/mpeg")


@pytest.fixture
def agent() -> ScriptedAgent:
    return ScriptedAgent()


@pytest.fixture
def voice() -> FakeVoice:
    return FakeVoice()


@pytest.fixture
def chat(
    repository: InMemoryCandidateRepository,
    agent: ScriptedAgent,
    clock: FixedClock,
    app_config: AppConfig,
) -> WebChatService:
    orchestrator = ConversationOrchestrator(
        repository=repository,
        agent=agent,
        channel=WebChatChannel(),
        clock=clock,
        config=app_config,
    )
    return WebChatService(
        starter=orchestrator, handler=orchestrator, repository=repository, clock=clock
    )


def make_client(chat: WebChatService, voice: FakeVoice | None) -> TestClient:
    app = FastAPI()
    app.include_router(build_voice_router(chat=chat, voice=voice, limits=LIMITS))
    return TestClient(app)


@pytest.fixture
def client(chat: WebChatService, voice: FakeVoice) -> TestClient:
    return make_client(chat, voice)


def start(client: TestClient) -> VoiceTurn:
    response = client.post("/voice/start", json=START)
    assert response.status_code == 200
    return VoiceTurn.model_validate(response.json())


def send_audio(client: TestClient, candidate_id: str, audio: bytes, mime: str = "audio/wav"):
    return client.post(
        f"/voice/{candidate_id}/turn", files={"audio": ("respuesta.wav", audio, mime)}
    )


def test_start_speaks_the_greeting(client: TestClient, voice: FakeVoice) -> None:
    turn = start(client)

    assert turn.transcript is None
    assert [m.role for m in turn.chat.messages] == ["agent"]
    greeting = turn.chat.messages[0].text
    assert greeting.startswith("¡Hola Lucía! Soy Sofía")
    assert len(voice.spoken) == 1
    spoken, language = voice.spoken[0]
    assert spoken.startswith("¡Hola Lucía! Soy Sofía") and language == Language.ES
    assert turn.audio_mime == "audio/mpeg"
    assert base64.b64decode(turn.audio_base64 or "") == f"audio:{spoken}".encode()


def test_turn_transcribes_runs_the_orchestrator_and_speaks_the_reply(
    client: TestClient,
    voice: FakeVoice,
    agent: ScriptedAgent,
    repository: InMemoryCandidateRepository,
) -> None:
    first = start(client)
    voice.transcripts.append("Sí, acepto.")
    agent.queue(ExtractionOutcome(provided_fields=["consent"], updates={"consent": True}))
    audio = wav(1.5)

    response = send_audio(client, first.chat.candidate_id, audio)

    assert response.status_code == 200
    turn = VoiceTurn.model_validate(response.json())
    assert voice.heard == [audio]
    assert turn.transcript == "Sí, acepto."
    assert [m.role for m in turn.chat.messages] == ["agent", "candidate", "agent"]
    assert turn.chat.messages[1].text == "Sí, acepto."
    assert turn.chat.stage == "knockout"
    # Solo se habla lo nuevo de Sofía, no el saludo otra vez.
    assert voice.spoken[-1][0] == speakable(turn.chat.messages[-1].text)
    assert base64.b64decode(turn.audio_base64 or "").startswith(b"audio:")
    stored = repository.get(first.chat.candidate_id)
    assert stored is not None and stored.data.consent is True
    said = next(m for m in stored.messages if m.role == MessageRole.CANDIDATE)
    assert said.channel == ChannelName.WEB


def test_tts_failure_still_returns_the_conversation(
    client: TestClient, voice: FakeVoice, agent: ScriptedAgent
) -> None:
    first = start(client)
    voice.fail_tts = True
    voice.transcripts.append("sí")
    agent.queue(ExtractionOutcome(provided_fields=["consent"], updates={"consent": True}))

    response = send_audio(client, first.chat.candidate_id, wav(1))

    assert response.status_code == 200
    turn = VoiceTurn.model_validate(response.json())
    assert turn.audio_base64 is None and turn.audio_mime is None
    assert turn.audio_error
    assert len(turn.chat.messages) == 3


def test_stt_failure_is_502_and_the_agent_is_not_called(
    client: TestClient, voice: FakeVoice, agent: ScriptedAgent
) -> None:
    first = start(client)
    voice.fail_stt = True
    calls = len(agent.extract_calls)

    response = send_audio(client, first.chat.candidate_id, wav(1))

    assert response.status_code == 502
    assert len(agent.extract_calls) == calls


def test_silence_is_422_and_the_agent_is_not_called(
    client: TestClient, voice: FakeVoice, agent: ScriptedAgent
) -> None:
    first = start(client)
    voice.transcripts.append("   ")
    calls = len(agent.extract_calls)

    response = send_audio(client, first.chat.candidate_id, wav(1))

    assert response.status_code == 422
    assert "no se escuchó" in response.json()["detail"]
    assert len(agent.extract_calls) == calls


@pytest.mark.parametrize(
    ("audio", "mime", "status"),
    [
        (b"", "audio/wav", 422),
        (b"hola", "text/plain", 422),
        (b"RIFF-roto", "audio/wav", 422),
        (wav(6), "audio/wav", 413),
        (b"\x00" * 200_001, "audio/webm", 413),
    ],
    ids=["empty", "not-audio", "broken-wav", "too-long", "too-big"],
)
def test_audio_limits_are_checked_before_calling_elevenlabs(
    client: TestClient, voice: FakeVoice, audio: bytes, mime: str, status: int
) -> None:
    first = start(client)

    response = send_audio(client, first.chat.candidate_id, audio, mime)

    assert response.status_code == status
    assert voice.heard == []


def test_check_audio_accepts_short_wav_and_other_formats_by_size() -> None:
    check_audio(wav(2), "audio/wav", LIMITS)
    check_audio(b"\x1aE\xdf\xa3webm", "audio/webm", LIMITS)


def test_unknown_candidate_is_404(client: TestClient, voice: FakeVoice) -> None:
    assert send_audio(client, "nope", wav(1)).status_code == 404
    assert voice.heard == []


def test_closed_conversation_is_not_processed(
    client: TestClient, voice: FakeVoice, agent: ScriptedAgent
) -> None:
    first = start(client)
    voice.transcripts.append("no")
    agent.queue(ExtractionOutcome(provided_fields=["consent"], updates={"consent": False}))
    closed = VoiceTurn.model_validate(send_audio(client, first.chat.candidate_id, wav(1)).json())
    assert closed.chat.done
    heard = len(voice.heard)

    again = send_audio(client, first.chat.candidate_id, wav(1))

    assert again.status_code == 200
    assert len(voice.heard) == heard  # ni siquiera se transcribe


def test_without_key_voice_is_disabled(chat: WebChatService) -> None:
    client = make_client(chat, None)

    assert client.get("/voice/status").json() == {"enabled": False}
    response = client.post("/voice/start", json=START)
    assert response.status_code == 503
    assert "voz" in response.json()["detail"]
    assert send_audio(client, "any", wav(1)).status_code == 503


def test_status_enabled(client: TestClient) -> None:
    assert client.get("/voice/status").json() == {"enabled": True}


def test_app_mounts_voice_disabled_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from screening.api import create_app_from_env

    for name in ("WHATSAPP_VERIFY_TOKEN", "WHATSAPP_APP_SECRET", "ELEVENLABS_API_KEY"):
        monkeypatch.setenv(name, "")
    monkeypatch.delenv("SCREENING_REPOSITORY", raising=False)

    client = TestClient(create_app_from_env(agent=ScriptedAgent()))

    assert client.get("/voice/status").json() == {"enabled": False}


def test_voice_client_round_trip() -> None:
    view = {
        "candidate_id": "c1",
        "phone": "+34600111222",
        "status": "contacted",
        "stage": "welcome",
        "done": False,
        "messages": [{"role": "agent", "text": "¡Hola!", "at": "2026-10-22T10:00:00Z"}],
    }
    turn = {
        "transcript": "hola",
        "chat": view,
        "audio_base64": base64.b64encode(b"mp3").decode(),
        "audio_mime": "audio/mpeg",
        "audio_error": None,
    }
    seen: list[tuple[str, str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.read()))
        if request.url.path == "/voice/status":
            return httpx.Response(200, json={"enabled": True})
        if request.url.path == "/voice/boom/turn":
            return httpx.Response(502, json={"detail": "no se pudo transcribir"})
        return httpx.Response(200, json=turn)

    client = VoiceClient("http://api", transport=httpx.MockTransport(handler))

    assert client.enabled() is True
    started = client.start(full_name="Lucía", country="ES")
    assert started.chat.candidate_id == "c1"
    assert started.audio() == b"mp3"
    assert client.turn("c1", b"RIFFwav", "audio/wav").transcript == "hola"
    with pytest.raises(CrmApiError) as error:
        client.turn("boom", b"RIFFwav", "audio/wav")
    assert error.value.status_code == 502
    assert [(m, p) for m, p, _ in seen[:3]] == [
        ("GET", "/voice/status"),
        ("POST", "/voice/start"),
        ("POST", "/voice/c1/turn"),
    ]
    assert b"RIFFwav" in seen[2][2]


def test_voice_client_status_without_api_is_disabled() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    client = VoiceClient("http://api", transport=httpx.MockTransport(handler))

    assert client.enabled() is False
