"""Ciclo real TTS → STT contra ElevenLabs (spec 020). Se salta sin `ELEVENLABS_API_KEY`."""

from __future__ import annotations

import os

import pytest

from screening.adapters.elevenlabs import ElevenLabsSettings, ElevenLabsVoice
from screening.domain import Language

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("ELEVENLABS_API_KEY"), reason="falta ELEVENLABS_API_KEY"),
]


def test_tts_then_stt_round_trip() -> None:
    settings = ElevenLabsSettings.from_env()
    assert settings is not None
    voice = ElevenLabsVoice(settings)

    audio = voice.synthesize("Tengo moto propia y vivo en Madrid.", language=Language.ES)
    assert audio.mime_type.startswith("audio/")
    assert len(audio.data) > 1000

    transcript = voice.transcribe(audio.data, filename="sofia.mp3", content_type=audio.mime_type)

    text = transcript.text.lower()
    assert "moto" in text
    assert "madrid" in text
