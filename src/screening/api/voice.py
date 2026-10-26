"""Agente de voz en el navegador (spec 020): el chat web con oídos y boca de ElevenLabs.

- `GET /voice/status`: si la voz está habilitada (hay `ELEVENLABS_API_KEY`).
- `POST /voice/start`: la postulación del chat web, con el saludo hablado.
- `POST /voice/{id}/turn`: audio → Scribe → mismo turno que `/chat/{id}/messages` → TTS.

El audio de la respuesta viaja en el mismo JSON (base64): un viaje por turno y nada que
guardar en el servidor. La key de ElevenLabs nunca sale de la API.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import wave
from collections.abc import Mapping
from typing import Annotated, Protocol

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from screening.adapters.elevenlabs import SpeechAudio, Transcript, VoiceError, speakable
from screening.api.web_chat import (
    MAX_MESSAGE_CHARS,
    ChatView,
    StartChatRequest,
    WebChatService,
    is_closed,
    to_view,
)
from screening.domain import Candidate, Language, MessageRole

logger = logging.getLogger(__name__)

VOICE_PATH = "/voice"
DISABLED_DETAIL = "la voz no está habilitada en este despliegue (falta ELEVENLABS_API_KEY)"


class VoiceService(Protocol):
    """Lo que la API necesita de un proveedor de voz (hoy, `ElevenLabsVoice`)."""

    def transcribe(self, audio: bytes, *, filename: str, content_type: str) -> Transcript: ...

    def synthesize(self, text: str, *, language: Language) -> SpeechAudio: ...


class AudioLimits(BaseModel):
    max_bytes: int = 5_000_000
    max_seconds: float = 60.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> AudioLimits:
        values = {
            "max_bytes": env.get("VOICE_MAX_AUDIO_BYTES"),
            "max_seconds": env.get("VOICE_MAX_AUDIO_SECONDS"),
        }
        return cls(**{k: v for k, v in values.items() if v})


class VoiceStatus(BaseModel):
    enabled: bool


class VoiceTurn(BaseModel):
    transcript: str | None
    chat: ChatView
    audio_base64: str | None = None
    audio_mime: str | None = None
    audio_error: str | None = None

    def audio(self) -> bytes | None:
        return base64.b64decode(self.audio_base64) if self.audio_base64 else None


def check_audio(data: bytes, content_type: str, limits: AudioLimits) -> None:
    """Valida la grabación antes de gastar en ElevenLabs. La duración se mide solo en WAV."""
    if not data:
        raise HTTPException(status_code=422, detail="el audio está vacío")
    if not content_type.startswith("audio/"):
        raise HTTPException(status_code=422, detail="el archivo no es audio")
    if len(data) > limits.max_bytes:
        raise HTTPException(status_code=413, detail="la grabación es demasiado grande")
    if data[:4] != b"RIFF" and "wav" not in content_type:
        return
    try:
        with wave.open(io.BytesIO(data)) as recording:
            seconds = recording.getnframes() / recording.getframerate()
    except (wave.Error, EOFError, ZeroDivisionError) as e:
        raise HTTPException(status_code=422, detail="no se pudo leer el audio WAV") from e
    if seconds > limits.max_seconds:
        raise HTTPException(
            status_code=413, detail=f"la grabación dura más de {limits.max_seconds:g} s"
        )


def new_agent_text(candidate: Candidate) -> str:
    """Lo que Sofía dijo después del último mensaje del candidato, listo para sintetizar."""
    texts: list[str] = []
    for message in reversed(candidate.messages):
        if message.role == MessageRole.CANDIDATE:
            break
        if message.role == MessageRole.AGENT:
            texts.append(speakable(message.text))
    return " ".join(t for t in reversed(texts) if t)


def build_voice_router(
    *, chat: WebChatService, voice: VoiceService | None, limits: AudioLimits
) -> APIRouter:
    router = APIRouter(prefix=VOICE_PATH, tags=["voz"])

    def require_voice() -> VoiceService:
        if voice is None:
            raise HTTPException(status_code=503, detail=DISABLED_DETAIL)
        return voice

    def spoken(provider: VoiceService, candidate: Candidate, transcript: str | None) -> VoiceTurn:
        turn = VoiceTurn(transcript=transcript, chat=to_view(candidate))
        text = new_agent_text(candidate)
        if not text:
            return turn
        try:
            audio = provider.synthesize(text, language=candidate.language)
        except VoiceError as e:
            # El turno ya quedó guardado: se devuelve como texto.
            logger.warning("TTS falló para %s: %s", candidate.id, e)
            turn.audio_error = f"No se pudo generar el audio: {e}"
            return turn
        turn.audio_base64 = base64.b64encode(audio.data).decode()
        turn.audio_mime = audio.mime_type
        return turn

    @router.get("/status")
    def status() -> VoiceStatus:
        return VoiceStatus(enabled=voice is not None)

    # `def` y no `async def`: ElevenLabs y el orquestador son bloqueantes; FastAPI usa un hilo.
    @router.post("/start")
    def start(body: StartChatRequest) -> VoiceTurn:
        provider = require_voice()
        return spoken(provider, chat.start(body), None)

    @router.post("/{candidate_id}/turn")
    def turn(candidate_id: str, audio: Annotated[UploadFile, File()]) -> VoiceTurn:
        provider = require_voice()
        candidate = chat.load(candidate_id)
        if is_closed(candidate):
            return VoiceTurn(transcript=None, chat=to_view(candidate))
        data = audio.file.read(limits.max_bytes + 1)
        content_type = audio.content_type or "application/octet-stream"
        check_audio(data, content_type, limits)
        try:
            transcript = provider.transcribe(
                data, filename=audio.filename or "audio", content_type=content_type
            )
        except VoiceError as e:
            raise HTTPException(status_code=502, detail=f"No se pudo transcribir: {e}") from e
        text = transcript.text.strip()[:MAX_MESSAGE_CHARS]
        if not text:
            raise HTTPException(status_code=422, detail="no se escuchó nada; intenta de nuevo")
        return spoken(provider, chat.send(candidate_id, text), text)

    return router
