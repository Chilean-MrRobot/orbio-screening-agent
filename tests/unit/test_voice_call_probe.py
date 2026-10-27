"""Probe de la llamada (spec 022): conversa aunque ElevenLabs mande pings sin parar."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

PING_EVERY = 0.02


def load_probe() -> ModuleType:
    path = Path(__file__).parents[2] / "scripts" / "voice_call_probe.py"
    spec = importlib.util.spec_from_file_location("voice_call_probe", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # `@dataclass` busca su módulo en sys.modules
    spec.loader.exec_module(module)
    return module


class FakeAgentSocket:
    """El agente de ElevenLabs visto por WebSocket: saluda, responde cada `user_message` con
    la frase de espera y luego la respuesta, y entre medio manda pings y audio sin parar."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.queue: asyncio.Queue[str | bytes] = asyncio.Queue()
        self.ping_id = 0

    async def send(self, message: str) -> None:
        event = json.loads(message)
        self.sent.append(event)
        if event["type"] == "conversation_initiation_client_data":
            await self.respond("¡Hola Lucía!")
        elif event["type"] == "user_message":
            await self.respond("Vale, déjame anotarlo.")
            asyncio.get_running_loop().call_later(
                0.1, self.queue.put_nowait, self.response(f"Anotado: {event['text']}")
            )

    async def respond(self, text: str) -> None:
        await self.queue.put(self.response(text))

    @staticmethod
    def response(text: str) -> str:
        return json.dumps(
            {"type": "agent_response", "agent_response_event": {"agent_response": text}}
        )

    async def recv(self) -> str | bytes:
        try:
            return await asyncio.wait_for(self.queue.get(), timeout=PING_EVERY)
        except TimeoutError:
            self.ping_id += 1
            if self.ping_id % 2:
                return b"audio"
            return json.dumps({"type": "ping", "ping_event": {"event_id": self.ping_id}})


def test_pings_do_not_keep_the_turn_open() -> None:
    """Regresión: el silencio se medía desde el último evento y los pings (cada ~2 s en
    ElevenLabs) lo reiniciaban, así que el probe nunca enviaba el primer `user_message`."""
    probe = load_probe()
    ws = FakeAgentSocket()

    timings = asyncio.run(
        probe.converse_on(ws, {"candidate_id": "c1"}, ["sí", "Madrid"], quiet_seconds=0.3)
    )

    users = [e["text"] for e in ws.sent if e["type"] == "user_message"]
    assert users == ["sí", "Madrid"]
    assert any(e["type"] == "pong" for e in ws.sent)
    assert timings is not None and [t.said for t in timings] == [None, "sí", "Madrid"]
    for timing in timings[1:]:
        assert timing.first < timing.last  # frase de espera, después la respuesta
