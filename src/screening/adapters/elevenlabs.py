"""ElevenLabs por HTTP (spec 020): Speech-to-Text (Scribe) y Text-to-Speech.

La key solo vive en la API; nunca se registra ni aparece en los mensajes de error.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

from screening.domain import Language

BASE_URL = "https://api.elevenlabs.io"
API_KEY_ENV = "ELEVENLABS_API_KEY"

# Pictogramas, símbolos y selectores de variante: la voz los leería o los saltaría mal.
_EMOJI = re.compile(
    "[\U0001f000-\U0001faff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff\ufe0f\u200d\u20e3]+"
)
_MARKDOWN = re.compile(r"[*_`#>~]+")
_SPACES = re.compile(r"\s+")


class VoiceError(Exception):
    """ElevenLabs falló (HTTP, timeout o respuesta inválida). El mensaje es apto para el usuario."""


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str | None


@dataclass(frozen=True)
class SpeechAudio:
    data: bytes
    mime_type: str


class ElevenLabsSettings(BaseModel):
    api_key: SecretStr
    voice_id: str = "FGY2WhTYpPnrIDTdsKH5"  # Laura: voz femenina prediseñada, multilingüe
    tts_model: str = "eleven_flash_v2_5"  # multilingüe y de menor latencia
    stt_model: str = "scribe_v1"
    output_format: str = "mp3_44100_64"
    timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> ElevenLabsSettings | None:
        """Sin `ELEVENLABS_API_KEY`, `None`: la voz queda deshabilitada."""
        key = (env.get(API_KEY_ENV) or "").strip()
        if not key:
            return None
        optional = {
            "voice_id": env.get("ELEVENLABS_VOICE_ID"),
            "tts_model": env.get("ELEVENLABS_TTS_MODEL"),
            "stt_model": env.get("ELEVENLABS_STT_MODEL"),
            "output_format": env.get("ELEVENLABS_OUTPUT_FORMAT"),
            "timeout_seconds": env.get("ELEVENLABS_TIMEOUT_SECONDS"),
        }
        return cls(api_key=SecretStr(key), **{k: v for k, v in optional.items() if v})


class _SttResponse(BaseModel):
    text: str
    language_code: str | None = None


def speakable(text: str) -> str:
    """El texto de un mensaje sin emojis ni marcas de Markdown, para sintetizarlo."""
    text = _MARKDOWN.sub("", _EMOJI.sub(" ", text))
    return _SPACES.sub(" ", text).strip()


class ElevenLabsVoice:
    def __init__(
        self, settings: ElevenLabsSettings, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._settings = settings
        self._http = httpx.Client(
            base_url=BASE_URL,
            headers={"xi-api-key": settings.api_key.get_secret_value()},
            transport=transport,
            timeout=settings.timeout_seconds,
        )

    def transcribe(self, audio: bytes, *, filename: str, content_type: str) -> Transcript:
        """Scribe detecta el idioma solo (español o inglés en este demo)."""
        response = self._post(
            "/v1/speech-to-text",
            data={"model_id": self._settings.stt_model},
            files={"file": (filename, audio, content_type)},
        )
        try:
            body = _SttResponse.model_validate(response.json())
        except (ValueError, ValidationError) as e:
            raise VoiceError("ElevenLabs devolvió una transcripción inválida") from e
        return Transcript(text=body.text.strip(), language=body.language_code)

    def synthesize(self, text: str, *, language: Language) -> SpeechAudio:
        response = self._post(
            f"/v1/text-to-speech/{self._settings.voice_id}",
            params={"output_format": self._settings.output_format},
            json={
                "text": text,
                "model_id": self._settings.tts_model,
                "language_code": language.value,
            },
        )
        if not response.content:
            raise VoiceError("ElevenLabs devolvió un audio vacío")
        mime = response.headers.get("content-type", "audio/mpeg").split(";")[0]
        return SpeechAudio(data=response.content, mime_type=mime)

    def _post(self, path: str, **kwargs: object) -> httpx.Response:
        try:
            response = self._http.post(path, **kwargs)  # type: ignore[arg-type]
        except httpx.TimeoutException as e:
            raise VoiceError("ElevenLabs tardó demasiado en responder") from e
        except httpx.HTTPError as e:
            raise VoiceError(f"no se pudo conectar con ElevenLabs ({type(e).__name__})") from e
        if response.is_error:
            raise VoiceError(f"ElevenLabs respondió {response.status_code}: {_detail(response)}")
        return response


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        return response.text[:200]
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("status") or detail)[:200]
    return str(detail)[:200]
