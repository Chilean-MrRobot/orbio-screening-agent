"""Adaptador de ElevenLabs Agents y alta idempotente del agente (spec 022), sin red."""

from __future__ import annotations

import json

import httpx
import pytest

from screening.adapters.elevenlabs import VoiceError
from screening.adapters.elevenlabs_agents import (
    AGENT_TTS_MODEL,
    LLM_SECRET_NAME,
    AgentSpec,
    ElevenLabsAgents,
    build_agent_config,
    sync_agent,
)
from screening.config import AppConfig
from screening.conversation.messages import agent_first_message
from screening.domain import Language

SPEC = AgentSpec(
    name="Sofía · Grupo Sazón (demo)",
    api_url="https://api.example.run.app/",
    voice_id="voice-1",
    tts_model="eleven_flash_v2_5",
    first_messages={Language.ES: "¡Hola {{first_name}}!", Language.EN: "Hi {{first_name}}!"},
)


class FakeElevenLabs:
    """Simula los endpoints de secretos y agentes que usa el script."""

    def __init__(self) -> None:
        self.secrets: dict[str, dict] = {}
        self.agents: dict[str, dict] = {}
        self.requests: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.requests.append((method, path))
        assert request.headers["xi-api-key"]
        body = json.loads(request.content) if request.content else {}
        if path == "/v1/convai/secrets" and method == "GET":
            secrets = [{"secret_id": k, "name": v["name"]} for k, v in self.secrets.items()]
            return httpx.Response(200, json={"secrets": secrets})
        if path == "/v1/convai/secrets" and method == "POST":
            assert body["type"] == "new"
            secret_id = f"sec-{len(self.secrets) + 1}"
            self.secrets[secret_id] = body
            return httpx.Response(200, json={"secret_id": secret_id, "name": body["name"]})
        if path.startswith("/v1/convai/secrets/") and method == "PATCH":
            assert body["type"] == "update"
            self.secrets[path.rsplit("/", 1)[1]] = body
            return httpx.Response(200, json={})
        if path == "/v1/convai/agents" and method == "GET":
            search = request.url.params.get("search", "")
            agents = [
                {"agent_id": k, "name": v["name"]}
                for k, v in self.agents.items()
                if search in v["name"]
            ]
            return httpx.Response(200, json={"agents": agents})
        if path == "/v1/convai/agents/create":
            agent_id = f"agent-{len(self.agents) + 1}"
            self.agents[agent_id] = body
            return httpx.Response(200, json={"agent_id": agent_id})
        if path.startswith("/v1/convai/agents/") and method == "PATCH":
            agent_id = path.rsplit("/", 1)[1]
            if agent_id not in self.agents:
                return httpx.Response(404, json={"detail": {"message": "not found"}})
            self.agents[agent_id] = body
            return httpx.Response(200, json={"agent_id": agent_id})
        if path == "/v1/convai/conversation/token":
            return httpx.Response(200, json={"token": f"tok-{request.url.params['agent_id']}"})
        if path == "/v1/convai/conversation/get-signed-url":
            return httpx.Response(200, json={"signed_url": "wss://signed"})
        return httpx.Response(404, json={"detail": "unexpected"})


@pytest.fixture
def fake() -> FakeElevenLabs:
    return FakeElevenLabs()


@pytest.fixture
def agents(fake: FakeElevenLabs) -> ElevenLabsAgents:
    return ElevenLabsAgents("key", transport=httpx.MockTransport(fake))


def test_conversation_token_and_signed_url(agents: ElevenLabsAgents) -> None:
    assert agents.conversation_token("agent-7") == "tok-agent-7"
    assert agents.signed_url("agent-7") == "wss://signed"


def test_errors_are_voice_errors_without_the_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        detail = {"detail": {"status": "missing_permissions", "message": "convai_write"}}
        return httpx.Response(401, json=detail)

    agents = ElevenLabsAgents("key-xyz", transport=httpx.MockTransport(handler))

    with pytest.raises(VoiceError) as error:
        agents.conversation_token("agent-1")
    assert "401" in str(error.value) and "convai_write" in str(error.value)
    assert "key-xyz" not in str(error.value)


def test_agent_config_points_to_our_llm_with_the_secret() -> None:
    config = build_agent_config(SPEC, secret_id="sec-1")

    agent = config["conversation_config"]["agent"]
    assert agent["language"] == "es"
    assert agent["first_message"] == "¡Hola {{first_name}}!"
    prompt = agent["prompt"]
    assert "candidate_id: {{candidate_id}}" in prompt["prompt"]
    assert prompt["llm"] == "custom-llm"
    assert prompt["custom_llm"]["url"] == "https://api.example.run.app/voice/llm/v1"
    assert prompt["custom_llm"]["api_key"] == {"secret_id": "sec-1"}
    assert "end_call" in prompt["built_in_tools"]
    # Nuestro turno tarda varios segundos: el máximo antes de la cascada, y sin LLM de
    # respaldo (otro modelo respondería sin el orquestador, las reglas ni la ficha).
    assert prompt["cascade_timeout_seconds"] == 15
    assert prompt["backup_llm_config"] == {"preference": "disabled"}
    tts = config["conversation_config"]["tts"]
    # Calidad de voz antes que ~100 ms de latencia (la voz sonaba cortada en el teléfono).
    assert tts == {
        "voice_id": "voice-1",
        "model_id": "eleven_flash_v2_5",
        "optimize_streaming_latency": 0,
        "agent_output_audio_format": "pcm_24000",
    }
    english = config["conversation_config"]["language_presets"]["en"]["overrides"]["agent"]
    assert english == {"first_message": "Hi {{first_name}}!", "language": "en"}
    override = config["platform_settings"]["overrides"]["conversation_config_override"]
    assert override["agent"]["language"] is True


def test_sync_creates_then_updates_without_duplicates(
    agents: ElevenLabsAgents, fake: FakeElevenLabs
) -> None:
    first = sync_agent(agents, SPEC, llm_secret="s1")

    assert first.created is True
    assert list(fake.agents) == [first.agent_id]
    assert [s["name"] for s in fake.secrets.values()] == [LLM_SECRET_NAME]

    second = sync_agent(agents, SPEC, llm_secret="s2")

    assert second.created is False
    assert second.agent_id == first.agent_id
    assert len(fake.agents) == 1 and len(fake.secrets) == 1
    assert next(iter(fake.secrets.values()))["value"] == "s2"


def test_sync_uses_the_given_agent_id(agents: ElevenLabsAgents, fake: FakeElevenLabs) -> None:
    created = sync_agent(agents, SPEC, llm_secret="s1")

    result = sync_agent(agents, SPEC, llm_secret="s1", agent_id=created.agent_id)

    assert result.agent_id == created.agent_id and result.created is False
    assert ("GET", "/v1/convai/agents") not in fake.requests[-3:]


def test_sync_recreates_when_the_given_agent_id_does_not_exist(
    agents: ElevenLabsAgents, fake: FakeElevenLabs
) -> None:
    result = sync_agent(agents, SPEC, llm_secret="s1", agent_id="gone")

    assert result.created is True
    assert result.agent_id in fake.agents


def test_first_message_comes_from_the_first_contact_template(app_config: AppConfig) -> None:
    text = agent_first_message(app_config, Language.ES)

    assert text.startswith("¡Hola {{first_name}}! Soy Sofía")
    assert "{agent_name}" not in text
    assert (
        "👋"
        not in build_agent_config(
            SPEC.model_copy(update={"first_messages": {Language.ES: text}}), secret_id="s"
        )["conversation_config"]["agent"]["first_message"]
    )
    assert agent_first_message(app_config, Language.EN).startswith("Hi {{first_name}}!")


def test_deploy_script_runs_sync(
    monkeypatch: pytest.MonkeyPatch, fake: FakeElevenLabs, capsys: pytest.CaptureFixture[str]
) -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).parents[2] / "deploy" / "elevenlabs_agent.py"
    spec = importlib.util.spec_from_file_location("elevenlabs_agent", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    env = {"ELEVENLABS_API_KEY": "el-key-123", "VOICE_LLM_SECRET": "llm-secret-456"}

    code = module.main(
        ["--api-url", "https://api.example"], env=env, transport=httpx.MockTransport(fake)
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "ELEVENLABS_AGENT_ID=agent-1" in out
    assert "llm-secret-456" not in out and "el-key-123" not in out
    agent = next(iter(fake.agents.values()))
    # El TTS de la llamada no sigue a ELEVENLABS_TTS_MODEL (flash, para la voz por turnos).
    assert agent["conversation_config"]["tts"]["model_id"] == AGENT_TTS_MODEL
    assert module.main(["--api-url", "https://api.example"], env={"ELEVENLABS_API_KEY": "k"}) == 2
