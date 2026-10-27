"""Adaptador de ElevenLabs (spec 020): STT y TTS por HTTP, sin red."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from screening.adapters.elevenlabs import (
    ElevenLabsSettings,
    ElevenLabsVoice,
    SpeechAudio,
    Transcript,
    VoiceError,
    speakable,
)
from screening.domain import Language

SETTINGS = ElevenLabsSettings(
    api_key=SecretStr("secret-key"),
    voice_id="voice-1",
    tts_model="eleven_flash_v2_5",
    stt_model="scribe_v1",
    output_format="mp3_44100_64",
    timeout_seconds=5,
)


def voice_with(handler) -> ElevenLabsVoice:
    return ElevenLabsVoice(SETTINGS, transport=httpx.MockTransport(handler))


def test_transcribe_sends_multipart_with_model_and_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": " Tengo moto propia. ", "language_code": "spa"})

    transcript = voice_with(handler).transcribe(
        b"RIFF-audio", filename="respuesta.wav", content_type="audio/wav"
    )

    assert transcript == Transcript(text="Tengo moto propia.", language="spa")
    request = seen[0]
    assert request.method == "POST"
    assert request.url.path == "/v1/speech-to-text"
    assert request.headers["xi-api-key"] == "secret-key"
    body = request.read()
    assert b'name="model_id"' in body and b"scribe_v1" in body
    assert b'filename="respuesta.wav"' in body and b"RIFF-audio" in body


def test_synthesize_posts_text_model_and_language() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"ID3mp3", headers={"content-type": "audio/mpeg"})

    audio = voice_with(handler).synthesize("¡Hola! Soy Sofía.", language=Language.ES)

    assert audio == SpeechAudio(data=b"ID3mp3", mime_type="audio/mpeg")
    request = seen[0]
    assert request.url.path == "/v1/text-to-speech/voice-1"
    assert request.url.params["output_format"] == "mp3_44100_64"
    assert request.headers["xi-api-key"] == "secret-key"
    assert json.loads(request.content) == {
        "text": "¡Hola! Soy Sofía.",
        "model_id": "eleven_flash_v2_5",
        "language_code": "es",
    }


def test_http_error_is_a_clear_voice_error_without_the_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        detail = {"detail": {"status": "missing_permissions", "message": "no permission"}}
        return httpx.Response(401, json=detail)

    with pytest.raises(VoiceError) as error:
        voice_with(handler).transcribe(b"x", filename="a.wav", content_type="audio/wav")

    assert "401" in str(error.value)
    assert "no permission" in str(error.value)
    assert "secret-key" not in str(error.value)


def test_timeout_is_a_voice_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(VoiceError, match="tardó"):
        voice_with(handler).synthesize("hola", language=Language.ES)


def test_invalid_stt_response_is_a_voice_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    with pytest.raises(VoiceError, match="inválida"):
        voice_with(handler).transcribe(b"x", filename="a.wav", content_type="audio/wav")


def test_empty_tts_audio_is_a_voice_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"", headers={"content-type": "audio/mpeg"})

    with pytest.raises(VoiceError):
        voice_with(handler).synthesize("hola", language=Language.ES)


def test_settings_from_env() -> None:
    assert ElevenLabsSettings.from_env({}) is None
    assert ElevenLabsSettings.from_env({"ELEVENLABS_API_KEY": "  "}) is None

    settings = ElevenLabsSettings.from_env(
        {"ELEVENLABS_API_KEY": "sk-top", "ELEVENLABS_VOICE_ID": "v2", "ELEVENLABS_TTS_MODEL": "m2"}
    )

    assert settings is not None
    assert settings.api_key.get_secret_value() == "sk-top"
    assert settings.voice_id == "v2"
    assert settings.tts_model == "m2"
    assert settings.stt_model  # con valor por defecto
    assert "sk-top" not in repr(settings)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("¡Hola Lucía! 👋 Soy **Sofía** 🛵", "¡Hola Lucía! Soy Sofía"),
        ("Responde _sí_ o `no`.", "Responde sí o no."),
        ("🙂", ""),
        (
            "Soy Sofía, de Grupo Sazón 👋 Vi que postulaste.",
            "Soy Sofía, de Grupo Sazón. Vi que postulaste.",
        ),
        (
            "Hola {{first_name}}, ¿todo bien? 🙂 ¿Seguimos?",
            "Hola {{first_name}}, ¿todo bien? ¿Seguimos?",
        ),
    ],
)
def test_speakable_removes_emojis_and_markdown(text: str, expected: str) -> None:
    assert speakable(text) == expected
