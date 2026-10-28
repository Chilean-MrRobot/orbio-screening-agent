"""Prueba de punta a punta de la llamada en tiempo real (spec 022), sin micrófono.

Crea una postulación con `POST /voice/call/start` en la API desplegada, abre una sesión del
agente de ElevenLabs por WebSocket (URL firmada) y conversa por texto (`user_message`):
ElevenLabs llama a nuestro Custom LLM igual que en una llamada de voz. Mide la latencia de
cada turno desde el lado de ElevenLabs y analiza el audio de Sofía: cortes al reproducirlo en
vivo y pausas dentro de cada turno (con `--record`, además, un WAV por turno).

Uso: python scripts/voice_call_probe.py --api-url <URL de la API> [--turn "texto"]...
     [--record <carpeta>]
Lee `ELEVENLABS_API_KEY` y `ELEVENLABS_AGENT_ID` del `.env` o del entorno.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import os
import sys
import time
import wave
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx
import websockets
from dotenv import load_dotenv

from screening.adapters.elevenlabs_agents import ElevenLabsAgents

DEFAULT_TURNS = ["Sí, claro, tengo tiempo.", "Sí, acepto.", "Vivo en Madrid."]


class Socket(Protocol):
    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...


@dataclass
class TurnTiming:
    said: str | None
    first: float  # segundos hasta la primera respuesta del agente (p. ej. la frase de espera)
    last: float  # segundos hasta la última (la respuesta del orquestador)
    # Audio del agente: (segundos desde el mensaje del candidato, PCM de 16 bits mono).
    audio: list[tuple[float, bytes]] = field(default_factory=list)
    sample_rate: int = 16000  # de `agent_output_audio_format` (p. ej. pcm_24000)


def playback_gaps(
    chunks: list[tuple[float, bytes]], sample_rate: int, *, min_gap: float = 0.05
) -> list[tuple[float, float]]:
    """Cortes que oiría un reproductor que toca el audio apenas llega: (en qué segundo del
    audio, cuánto dura el corte). Un corte es un chunk que llega cuando el anterior ya sonó."""
    gaps: list[tuple[float, float]] = []
    played, ends_at = 0.0, None
    for arrival, pcm in chunks:
        duration = len(pcm) / (2 * sample_rate)
        if ends_at is not None and arrival - ends_at >= min_gap:
            gaps.append((round(played, 2), round(arrival - ends_at, 2)))
        ends_at = max(ends_at or arrival, arrival) + duration
        played += duration
    return gaps


def silences(
    pcm: bytes, sample_rate: int, *, min_silence: float = 0.35, threshold: int = 300
) -> list[tuple[float, float]]:
    """Pausas dentro del audio (ventanas de 20 ms bajo `threshold` de RMS): (inicio, duración)."""
    samples = array("h", pcm[: len(pcm) // 2 * 2])
    window = sample_rate // 50
    found: list[tuple[float, float]] = []
    start: int | None = None
    for i in range(0, len(samples), window):
        part = samples[i : i + window]
        quiet = math.sqrt(sum(s * s for s in part) / max(1, len(part))) < threshold
        if quiet and start is None:
            start = i
        elif not quiet and start is not None:
            if (i - start) / sample_rate >= min_silence:
                found.append((round(start / sample_rate, 2), round((i - start) / sample_rate, 2)))
            start = None
    return found  # el silencio final no cuenta: es el fin del turno


def write_wav(path: Path, pcm: bytes, sample_rate: int) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)


async def converse(
    signed_url: str, variables: dict[str, str], turns: list[str], record: Path | None
) -> int:
    async with websockets.connect(signed_url, max_size=None) as ws:
        timings = await converse_on(ws, variables, turns)
    if timings is None:
        return 1
    print("\nLatencia por turno (desde el mensaje del candidato):")
    for t in timings[1:]:
        print(f"  primera respuesta {t.first:5.1f} s · última {t.last:5.1f} s · {t.said}")
    print("\nAudio por turno (cortes al reproducir en vivo y pausas dentro del audio):")
    for i, t in enumerate(timings):
        pcm = b"".join(chunk for _, chunk in t.audio)
        gaps = playback_gaps(t.audio, t.sample_rate)
        pauses = silences(pcm, t.sample_rate)
        print(
            f"  turno {i}: {len(pcm) / (2 * t.sample_rate):4.1f} s de audio en {len(t.audio)} "
            f"chunks · cortes {gaps} · pausas {pauses}"
        )
        if record:
            record.mkdir(parents=True, exist_ok=True)
            write_wav(record / f"turno_{i}.wav", pcm, t.sample_rate)
    if record:
        print(f"\nAudio en {record}")
    return 0


async def converse_on(
    ws: Socket,
    variables: dict[str, str],
    turns: list[str],
    *,
    quiet_seconds: float = 4.0,
    turn_timeout: float = 45.0,
) -> list[TurnTiming] | None:
    """Un turno termina cuando el agente respondió y luego no dijo nada más en `quiet_seconds`
    (la frase de espera y la respuesta pueden llegar separadas).

    El silencio se mide desde la última `agent_response`, no desde el último evento:
    ElevenLabs manda `ping` cada ~2 s y audio, que no cuentan como respuesta.
    """
    init = {"type": "conversation_initiation_client_data", "dynamic_variables": variables}
    await ws.send(json.dumps(init))
    timings: list[TurnTiming] = []
    sample_rate = 16000
    for said in [None, *turns]:  # None: el primer mensaje del agente
        if said is not None:
            print(f"Candidato: {said}")
            await ws.send(json.dumps({"type": "user_message", "text": said}))
        sent_at = time.monotonic()
        responses: list[float] = []
        audio: list[tuple[float, bytes]] = []
        while True:
            now = time.monotonic()
            deadline = responses[-1] + quiet_seconds if responses else sent_at + turn_timeout
            if now >= deadline:
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=deadline - now)
            except TimeoutError:
                break
            if isinstance(raw, bytes):
                continue
            event = json.loads(raw)
            kind = event.get("type")
            if kind == "ping":
                ping = event.get("ping_event", {})
                await ws.send(json.dumps({"type": "pong", "event_id": ping.get("event_id")}))
            elif kind == "agent_response":
                text = event.get("agent_response_event", {}).get("agent_response", "")
                responses.append(time.monotonic())
                print(f"  Sofía (+{responses[-1] - sent_at:.1f}s): {text}")
            elif kind == "audio":
                chunk = event.get("audio_event", {}).get("audio_base_64") or ""
                audio.append((time.monotonic() - sent_at, base64.b64decode(chunk)))
            elif kind == "conversation_initiation_metadata":
                metadata = event.get("conversation_initiation_metadata_event", {})
                fmt = str(metadata.get("agent_output_audio_format") or "")
                if fmt.startswith("pcm_"):
                    sample_rate = int(fmt.removeprefix("pcm_"))
            elif kind == "error" or "error" in event:
                print(f"  error: {event}")
                return None
        if not responses:
            print(f"  (sin respuesta del agente en {turn_timeout:.0f} s)")
            return None
        timings.append(
            TurnTiming(said, responses[0] - sent_at, responses[-1] - sent_at, audio, sample_rate)
        )
    return timings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--turn", action="append", dest="turns")
    parser.add_argument("--record", type=Path, help="carpeta donde guardar un WAV por turno")
    args = parser.parse_args()
    load_dotenv()
    key, agent_id = os.environ.get("ELEVENLABS_API_KEY"), os.environ.get("ELEVENLABS_AGENT_ID")
    if not key or not agent_id:
        print("Faltan ELEVENLABS_API_KEY o ELEVENLABS_AGENT_ID", file=sys.stderr)
        return 2

    start = httpx.post(
        f"{args.api_url.rstrip('/')}/voice/call/start",
        json={"full_name": "Prueba Llamada", "country": "ES"},
        timeout=60,
    )
    start.raise_for_status()
    variables = start.json()["dynamic_variables"]
    print(f"Postulación {variables['candidate_id']} creada")
    signed_url = ElevenLabsAgents(key).signed_url(agent_id)
    return asyncio.run(converse(signed_url, variables, args.turns or DEFAULT_TURNS, args.record))


if __name__ == "__main__":
    raise SystemExit(main())
