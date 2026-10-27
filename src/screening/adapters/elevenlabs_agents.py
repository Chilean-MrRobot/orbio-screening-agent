"""ElevenLabs Agents (Conversational AI) por HTTP (spec 022).

- En la API: el token de conversación (WebRTC) o la URL firmada (WebSocket) para el navegador.
- En `deploy/elevenlabs_agent.py`: el secreto del workspace y el agente con Custom LLM
  apuntando a nuestro endpoint compatible con OpenAI, de forma idempotente.

La key nunca se registra ni aparece en los mensajes de error.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, SecretStr

from screening.adapters.elevenlabs import BASE_URL, VoiceError, _detail, speakable
from screening.domain import Language

LLM_SECRET_NAME = "orbio-voice-llm-secret"
LLM_PATH = "/voice/llm/v1"  # ElevenLabs le agrega /chat/completions
LLM_MODEL_ID = "orbio-screening-orchestrator"
# El prompt no decide nada: lo que dice Sofía sale del orquestador. Solo lleva el
# `candidate_id` (variable dinámica) para que nuestro endpoint encuentre la ficha.
AGENT_PROMPT = (
    "Eres Sofía, la asistente virtual de reclutamiento de Grupo Sazón. Las respuestas las "
    "genera el sistema de screening de Orbio.\n"
    "candidate_id: {{candidate_id}}\n"
)


class AgentSpec(BaseModel):
    name: str
    api_url: str
    voice_id: str
    tts_model: str
    first_messages: dict[Language, str]


class SyncResult(BaseModel):
    agent_id: str
    created: bool
    secret_id: str


class ElevenLabsAgents:
    def __init__(
        self,
        api_key: str | SecretStr,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 15.0,
    ) -> None:
        key = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        self._http = httpx.Client(
            base_url=BASE_URL, headers={"xi-api-key": key}, transport=transport, timeout=timeout
        )

    # --- Navegador ---------------------------------------------------------------------

    def conversation_token(self, agent_id: str) -> str:
        """Token de un solo uso para iniciar una sesión WebRTC desde el navegador."""
        body = self._json("GET", "/v1/convai/conversation/token", params={"agent_id": agent_id})
        return _field(body, "token")

    def signed_url(self, agent_id: str) -> str:
        """URL firmada para iniciar una sesión por WebSocket (alternativa a WebRTC)."""
        body = self._json(
            "GET", "/v1/convai/conversation/get-signed-url", params={"agent_id": agent_id}
        )
        return _field(body, "signed_url")

    # --- Alta del agente ---------------------------------------------------------------

    def find_secret(self, name: str) -> str | None:
        secrets = self._json("GET", "/v1/convai/secrets").get("secrets") or []
        return next((s["secret_id"] for s in secrets if s.get("name") == name), None)

    def create_secret(self, name: str, value: str) -> str:
        body = self._json(
            "POST", "/v1/convai/secrets", json={"type": "new", "name": name, "value": value}
        )
        return _field(body, "secret_id")

    def update_secret(self, secret_id: str, name: str, value: str) -> None:
        self._json(
            "PATCH",
            f"/v1/convai/secrets/{secret_id}",
            json={"type": "update", "name": name, "value": value},
        )

    def find_agent(self, name: str) -> str | None:
        agents = self._json("GET", "/v1/convai/agents", params={"search": name}).get("agents")
        return next((a["agent_id"] for a in agents or [] if a.get("name") == name), None)

    def create_agent(self, config: dict[str, Any]) -> str:
        return _field(self._json("POST", "/v1/convai/agents/create", json=config), "agent_id")

    def update_agent(self, agent_id: str, config: dict[str, Any]) -> bool:
        """`False` si el agente no existe (404)."""
        try:
            self._json("PATCH", f"/v1/convai/agents/{agent_id}", json=config)
        except _NotFound:
            return False
        return True

    def _json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.TimeoutException as e:
            raise VoiceError("ElevenLabs tardó demasiado en responder") from e
        except httpx.HTTPError as e:
            raise VoiceError(f"no se pudo conectar con ElevenLabs ({type(e).__name__})") from e
        if response.status_code == 404:
            raise _NotFound(f"ElevenLabs respondió 404: {_detail(response)}")
        if response.is_error:
            raise VoiceError(f"ElevenLabs respondió {response.status_code}: {_detail(response)}")
        try:
            body = response.json() if response.content else {}
        except ValueError as e:
            raise VoiceError("ElevenLabs devolvió una respuesta inválida") from e
        return body if isinstance(body, dict) else {}


class _NotFound(VoiceError):
    pass


def _field(body: dict[str, Any], name: str) -> str:
    value = body.get(name)
    if not isinstance(value, str) or not value:
        raise VoiceError(f"ElevenLabs no devolvió `{name}`")
    return value


def build_agent_config(spec: AgentSpec, *, secret_id: str) -> dict[str, Any]:
    """Configuración completa del agente: idioma `es` con preset `en`, voz de Sofía y
    Custom LLM hacia nuestra API (con `end_call` para colgar al cerrar la conversación)."""
    first = {lang: speakable(text) for lang, text in spec.first_messages.items()}
    presets = {
        lang.value: {"overrides": {"agent": {"first_message": text, "language": lang.value}}}
        for lang, text in first.items()
        if lang != Language.ES
    }
    return {
        "name": spec.name,
        "conversation_config": {
            "agent": {
                "first_message": first.get(Language.ES, ""),
                "language": Language.ES.value,
                "prompt": {
                    "prompt": AGENT_PROMPT,
                    "llm": "custom-llm",
                    "custom_llm": {
                        "url": spec.api_url.rstrip("/") + LLM_PATH,
                        "model_id": LLM_MODEL_ID,
                        "api_key": {"secret_id": secret_id},
                    },
                    # Un turno nuestro tarda varios segundos (extracción + respuesta): el
                    # máximo antes de la cascada (4 s por defecto), y sin LLM de respaldo,
                    # que respondería sin el orquestador, las reglas ni la ficha.
                    "cascade_timeout_seconds": 15,
                    "backup_llm_config": {"preference": "disabled"},
                    "built_in_tools": {
                        "end_call": {
                            "name": "end_call",
                            "description": "Cuelga cuando la conversación de screening terminó.",
                            "params": {"system_tool_type": "end_call"},
                        }
                    },
                },
            },
            "tts": {"voice_id": spec.voice_id, "model_id": spec.tts_model},
            "language_presets": presets,
        },
        "platform_settings": {
            "overrides": {"conversation_config_override": {"agent": {"language": True}}}
        },
    }


def sync_agent(
    client: ElevenLabsAgents, spec: AgentSpec, *, llm_secret: str, agent_id: str | None = None
) -> SyncResult:
    """Crea o actualiza el secreto y el agente. Idempotente: se puede correr de nuevo."""
    secret_id = client.find_secret(LLM_SECRET_NAME)
    if secret_id is None:
        secret_id = client.create_secret(LLM_SECRET_NAME, llm_secret)
    else:
        client.update_secret(secret_id, LLM_SECRET_NAME, llm_secret)

    config = build_agent_config(spec, secret_id=secret_id)
    existing = agent_id or client.find_agent(spec.name)
    if existing and client.update_agent(existing, config):
        return SyncResult(agent_id=existing, created=False, secret_id=secret_id)
    return SyncResult(agent_id=client.create_agent(config), created=True, secret_id=secret_id)
