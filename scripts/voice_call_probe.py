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

import httpx
import websockets
from dotenv import load_dotenv

from screening.adapters.elevenlabs_agents import ElevenLabsAgents

DEFAULT_TURNS = ["Sí, claro, tengo tiempo.", "Sí, acepto.", "Vivo en Madrid."]


async def converse(signed_url: str, variables: dict[str, str], turns: list[str]) -> int:
    async with websockets.connect(signed_url, max_size=None) as ws:
        init = {"type": "conversation_initiation_client_data", "dynamic_variables": variables}
        await ws.send(json.dumps(init))
        pending = [None, *turns]  # None: esperar el primer mensaje del agente
        sent_at = time.monotonic()
        while pending:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=45)
            except TimeoutError:
                print("  (sin respuesta del agente en 45 s)")
                return 1
            if isinstance(raw, bytes):
                continue
            event = json.loads(raw)
            kind = event.get("type")
            if kind == "ping":
                ping = event.get("ping_event", {})
                await ws.send(json.dumps({"type": "pong", "event_id": ping.get("event_id")}))
            elif kind == "agent_response":
                text = event.get("agent_response_event", {}).get("agent_response", "")
                print(f"  Sofía (+{time.monotonic() - sent_at:.1f}s): {text}")
                if pending[0] is None:  # una respuesta del agente por turno
                    pending.pop(0)
                    if pending:
                        print(f"Candidato: {pending[0]}")
                        await ws.send(json.dumps({"type": "user_message", "text": pending[0]}))
                        sent_at = time.monotonic()
                        pending[0] = None
            elif kind in ("conversation_initiation_metadata", "audio", "agent_response_correction"):
                continue
            elif kind == "error" or "error" in event:
                print(f"  error: {event}")
                return 1
    return 0


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
