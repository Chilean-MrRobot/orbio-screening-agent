"""Prueba de punta a punta de la llamada en tiempo real (spec 022), sin micrófono.

Crea una postulación con `POST /voice/call/start` en la API desplegada, abre una sesión del
agente de ElevenLabs por WebSocket (URL firmada) y conversa por texto (`user_message`):
ElevenLabs llama a nuestro Custom LLM igual que en una llamada de voz. Mide la latencia de
cada turno desde el lado de ElevenLabs.

Uso: python scripts/voice_call_probe.py --api-url <URL de la API> [--turn "texto"]...
Lee `ELEVENLABS_API_KEY` y `ELEVENLABS_AGENT_ID` del `.env` o del entorno.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
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


async def converse(signed_url: str, variables: dict[str, str], turns: list[str]) -> int:
    async with websockets.connect(signed_url, max_size=None) as ws:
        timings = await converse_on(ws, variables, turns)
    if timings is None:
        return 1
    print("\nLatencia por turno (desde el mensaje del candidato):")
    for t in timings[1:]:
        print(f"  primera respuesta {t.first:5.1f} s · última {t.last:5.1f} s · {t.said}")
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
    for said in [None, *turns]:  # None: el primer mensaje del agente
        if said is not None:
            print(f"Candidato: {said}")
            await ws.send(json.dumps({"type": "user_message", "text": said}))
        sent_at = time.monotonic()
        responses: list[float] = []
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
            elif kind == "error" or "error" in event:
                print(f"  error: {event}")
                return None
        if not responses:
            print(f"  (sin respuesta del agente en {turn_timeout:.0f} s)")
            return None
        timings.append(TurnTiming(said, responses[0] - sent_at, responses[-1] - sent_at))
    return timings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--turn", action="append", dest="turns")
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
    return asyncio.run(converse(signed_url, variables, args.turns or DEFAULT_TURNS))


if __name__ == "__main__":
    raise SystemExit(main())
