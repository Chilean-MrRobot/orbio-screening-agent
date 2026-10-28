"""Llamada en tiempo real (spec 022): ElevenLabs pone el audio y nuestro orquestador, el cerebro.

- `GET /voice/call/status`, `POST /voice/call/start` y `POST /voice/call/{id}/token`: la
  postulación y un token de conversación de ElevenLabs para el navegador (la key no sale).
- `POST /voice/llm/v1/chat/completions`: el "Custom LLM" del agente de ElevenLabs. Compatible
  con OpenAI (JSON o SSE) y protegido con `VOICE_LLM_SECRET`. Toma lo último que dijo el
  candidato, lo pasa por el mismo camino que el chat web y devuelve lo nuevo de Sofía.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, SecretStr

from screening.adapters.elevenlabs import VoiceError, speakable
from screening.api.voice import VOICE_PATH, new_agent_text
from screening.api.web_chat import (
    MAX_MESSAGE_CHARS,
    ChatView,
    StartChatRequest,
    WebChatService,
    is_closed,
    to_view,
)
from screening.config import AppConfig
from screening.conversation.messages import render_template, template_variables
from screening.domain import Candidate, MessageRole

logger = logging.getLogger(__name__)

DISABLED_DETAIL = (
    "la llamada no está habilitada en este despliegue "
    "(faltan ELEVENLABS_API_KEY, ELEVENLABS_AGENT_ID o VOICE_LLM_SECRET)"
)
_CANDIDATE_ID = re.compile(r"candidate_id:\s*([\w-]+)")
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
# Frases de espera que se alternan por turno: la misma en cada turno suena a máquina.
WAIT_TEMPLATES = ("voice_wait", "voice_wait_2", "voice_wait_3")
# Spec 023: muletillas sueltas ("Eh.", "Mmm…", "este"). ElevenLabs corta el turno en una
# vacilación y llama con la transcripción provisional; no es una respuesta.
_FILLER = re.compile(r"(?:e+h*m*|m+|h+m+|u+h+|u+m+|a+h+|o+h+|este)")
_WORD = re.compile(r"[^\W\d_]+")


class ConversationTokens(Protocol):
    def conversation_token(self, agent_id: str) -> str: ...


class CallSettings(BaseModel):
    agent_id: str | None = None
    llm_secret: SecretStr | None = None
    wait_after_seconds: float = 1.5  # frase de espera (`voice_wait`); 0 la desactiva
    wait_repeat_seconds: float = 6.0  # segunda frase (`voice_wait_more`); 0 la desactiva
    # Chunk vacío mientras corre el turno, para que ElevenLabs vea el stream vivo; 0 lo apaga.
    heartbeat_seconds: float = 1.0
    # Cuánto espera un pedido a que termine el turno en curso del mismo candidato.
    busy_wait_seconds: float = 20.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> CallSettings:
        values = {
            "agent_id": env.get("ELEVENLABS_AGENT_ID"),
            "llm_secret": env.get("VOICE_LLM_SECRET"),
            "wait_after_seconds": env.get("VOICE_WAIT_AFTER_SECONDS"),
            "wait_repeat_seconds": env.get("VOICE_WAIT_REPEAT_SECONDS"),
            "heartbeat_seconds": env.get("VOICE_HEARTBEAT_SECONDS"),
            "busy_wait_seconds": env.get("VOICE_BUSY_WAIT_SECONDS"),
        }
        return cls.model_validate({k: v.strip() for k, v in values.items() if v and v.strip()})


class CallStatus(BaseModel):
    enabled: bool


class CallSession(BaseModel):
    chat: ChatView
    conversation_token: str
    dynamic_variables: dict[str, str]
    language: str


@dataclass(frozen=True)
class TurnResult:
    text: str
    closed: bool


def candidate_id_from(messages: list[dict[str, Any]]) -> str | None:
    """El agente de ElevenLabs pone `candidate_id: {{candidate_id}}` en su prompt de sistema."""
    for message in messages:
        if message.get("role") == "system":
            match = _CANDIDATE_ID.search(_content(message))
            if match:
                return match.group(1)
    return None


def user_text_from(messages: list[dict[str, Any]]) -> str:
    """Los mensajes del usuario después del último de Sofía (varios si interrumpió)."""
    texts: list[str] = []
    for message in reversed(messages):
        if message.get("role") == "assistant":
            break
        if message.get("role") == "user":
            texts.append(_content(message).strip())
    return " ".join(t for t in reversed(texts) if t)


def _content(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, list):  # formato con partes: [{"type": "text", "text": ...}]
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content if isinstance(content, str) else ""


def spoken_after_last_user(messages: list[dict[str, Any]]) -> str:
    """Lo que ElevenLabs ya dijo en este turno (frase de espera, una respuesta cortada)."""
    texts: list[str] = []
    for message in reversed(messages):
        if message.get("role") == "user":
            break
        if message.get("role") == "assistant":
            texts.append(_content(message))
    return speakable(" ".join(reversed(texts)))


def previous_assistant(messages: list[dict[str, Any]]) -> str:
    """El último mensaje de Sofía antes de lo que acaba de decir el usuario."""
    seen_user = False
    for message in reversed(messages):
        if message.get("role") == "user":
            seen_user = True
        elif message.get("role") == "assistant" and seen_user:
            return speakable(_content(message))
    return ""


def unspoken(reply: str, said: str) -> str:
    """Las frases de `reply` que ElevenLabs todavía no dijo."""
    already = set(_sentences(said))
    return " ".join(s for s in _sentences(reply) if s not in already)


def is_retry(candidate: Candidate, text: str, messages: list[dict[str, Any]]) -> bool:
    """¿Es el mismo mensaje del candidato que ya está en la ficha (reintento de ElevenLabs)?

    Si el candidato repite de verdad la misma respuesta ("sí" y "sí"), antes escuchó la
    respuesta de Sofía al primero, y ese texto viene en el historial como su último mensaje.
    """
    said = [m for m in candidate.messages if m.role == MessageRole.CANDIDATE]
    if not said or said[-1].text.strip() != text:
        return False
    reply = new_agent_text(candidate)
    return not reply or reply not in previous_assistant(messages)


def is_filler(text: str) -> bool:
    """¿Solo muletillas? "Eh." o "Mmm, este…" sí; "Eh, sí" no."""
    words = _WORD.findall(text.lower())
    return bool(words) and all(_FILLER.fullmatch(w) for w in words)


def wait_template(messages: list[dict[str, Any]]) -> str:
    """La frase de espera de este turno, según cuántas veces habló el candidato.

    Sale del historial de ElevenLabs y no de la ficha: un reintento del mismo turno elige
    la misma frase (y no la repite si ya se dijo).
    """
    answers = sum(1 for m in messages if m.get("role") == "user")
    return WAIT_TEMPLATES[max(0, answers - 1) % len(WAIT_TEMPLATES)]


def last_agent_text(candidate: Candidate) -> str:
    agent = [m for m in candidate.messages if m.role == MessageRole.AGENT]
    return speakable(agent[-1].text) if agent else ""


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text) if s.strip()]


def build_voice_call_router(
    *,
    chat: WebChatService,
    tokens: ConversationTokens | None,
    settings: CallSettings,
    config: AppConfig,
    sleep: Callable[[float], None] = time.sleep,
) -> APIRouter:
    router = APIRouter(prefix=VOICE_PATH, tags=["llamada en tiempo real"])
    enabled = tokens is not None and bool(settings.agent_id) and settings.llm_secret is not None

    # --- Inicio de llamada -------------------------------------------------------------

    def session(candidate: Candidate) -> CallSession:
        if not enabled or tokens is None or settings.agent_id is None:
            raise HTTPException(status_code=503, detail=DISABLED_DETAIL)
        try:
            token = tokens.conversation_token(settings.agent_id)
        except VoiceError as e:
            raise HTTPException(status_code=502, detail=f"No se pudo iniciar: {e}") from e
        first_name = template_variables(candidate, config)["first_name"]
        return CallSession(
            chat=to_view(candidate),
            conversation_token=token,
            dynamic_variables={"candidate_id": candidate.id, "first_name": first_name},
            language=candidate.language.value,
        )

    @router.get("/call/status")
    def status() -> CallStatus:
        return CallStatus(enabled=enabled)

    @router.post("/call/start")
    def start(body: StartChatRequest) -> CallSession:
        if not enabled:
            raise HTTPException(status_code=503, detail=DISABLED_DETAIL)
        return session(chat.start(body))

    @router.post("/call/{candidate_id}/token")
    def token(candidate_id: str) -> CallSession:
        candidate = chat.load(candidate_id)
        if is_closed(candidate):
            raise HTTPException(status_code=409, detail="la conversación ya terminó")
        return session(candidate)

    # --- Custom LLM ----------------------------------------------------------------------

    def authorize(authorization: str | None) -> None:
        if settings.llm_secret is None:
            raise HTTPException(status_code=503, detail="falta VOICE_LLM_SECRET")
        scheme, _, given = (authorization or "").partition(" ")
        expected = settings.llm_secret.get_secret_value().encode()
        if scheme.lower() != "bearer" or not hmac.compare_digest(given.encode(), expected):
            raise HTTPException(status_code=401, detail="no autorizado")

    def send(candidate: Candidate, text: str) -> Candidate | None:
        """Procesa el texto; si otro turno tiene el lease, lo espera. `None` si no llegó."""
        deadline = time.monotonic() + settings.busy_wait_seconds
        while True:
            try:
                return chat.send(candidate.id, text)
            except HTTPException as e:
                if e.status_code != 409:
                    raise
                if time.monotonic() >= deadline:
                    return None
                sleep(0.3)

    def wait_for_reply(candidate_id: str) -> Candidate | None:
        """Espera a que el turno en curso deje la respuesta de Sofía en la ficha (Firestore)."""
        deadline = time.monotonic() + settings.busy_wait_seconds
        while True:
            candidate = chat.load(candidate_id)
            if new_agent_text(candidate) or is_closed(candidate):
                return candidate
            if time.monotonic() >= deadline:
                return None
            sleep(0.3)

    def respond(candidate: Candidate, messages: list[dict[str, Any]]) -> TurnResult:
        """Bloqueante (LLM): corre en un hilo. Nunca devuelve un texto vacío.

        - Texto nuevo del candidato: pasa por el orquestador (mismo camino que el chat web).
        - Reintento de ElevenLabs o pedido sin texto nuevo: no se procesa otra vez; se espera
          el turno en curso y se devuelve lo que Sofía todavía no dijo.
        """
        text = user_text_from(messages)[:MAX_MESSAGE_CHARS]
        if text and is_filler(text) and not is_closed(candidate):
            listening = speakable(render_template("voice_listening", candidate, config))
            return TurnResult(listening, False)
        if text and not is_retry(candidate, text, messages):
            updated = send(candidate, text)
            if updated is None:
                busy = speakable(render_template("voice_busy", candidate, config))
                return TurnResult(busy, False)
        else:
            updated = wait_for_reply(candidate.id)
            if updated is None:
                wait = speakable(render_template(wait_template(messages), candidate, config))
                return TurnResult(wait, False)
        said = spoken_after_last_user(messages)
        reply = unspoken(new_agent_text(updated), said)
        # Nada pendiente (p. ej. ElevenLabs pide seguir tras un silencio): repite la pregunta.
        return TurnResult(reply or last_agent_text(updated), is_closed(updated))

    @router.post("/llm/v1/chat/completions", response_model=None)
    async def completions(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict[str, Any] | StreamingResponse:
        authorize(authorization)
        try:
            body = await request.json()
        except ValueError as e:
            raise HTTPException(status_code=400, detail="JSON inválido") from e
        messages = body.get("messages") if isinstance(body, dict) else None
        if not isinstance(messages, list):
            raise HTTPException(status_code=400, detail="faltan los mensajes")
        candidate_id = candidate_id_from(messages)
        if candidate_id is None:
            raise HTTPException(status_code=400, detail="falta candidate_id en el prompt")
        candidate = await run_in_threadpool(chat.load, candidate_id)
        tool_names = {(t.get("function") or {}).get("name") for t in body.get("tools") or []}
        end_call = "end_call" in tool_names
        completion = _Completion(str(body.get("model") or "orbio"))

        if end_call and is_closed(candidate):
            # Spec 023: Sofía ya se despidió. ElevenLabs vuelve a pedir (p. ej. con la
            # transcripción final): solo se corta, sin repetir el cierre.
            if not body.get("stream"):
                return completion.message("", end_call=True)

            async def hang_up() -> AsyncIterator[str]:
                yield completion.chunk({"role": "assistant", "content": ""})
                yield completion.chunk({"tool_calls": [completion.end_call(index=0)]})
                yield completion.chunk({}, finish_reason="tool_calls")
                yield "data: [DONE]\n\n"

            return StreamingResponse(hang_up(), media_type="text/event-stream")

        if not body.get("stream"):
            started = time.monotonic()
            result = await run_in_threadpool(respond, candidate, messages)
            _log_turn(candidate.id, started, first_chunk=None, fillers=0, heartbeats=0)
            return completion.message(result.text, end_call=end_call and result.closed)

        # Frases de espera por tiempo transcurrido, salvo las que ElevenLabs ya dijo.
        said = spoken_after_last_user(messages)
        fillers = [
            (seconds, speakable(render_template(key, candidate, config)))
            for seconds, key in (
                (settings.wait_after_seconds, wait_template(messages)),
                (settings.wait_repeat_seconds, "voice_wait_more"),
            )
            if seconds > 0
        ]
        fillers = sorted((s, t) for s, t in fillers if t and t not in said)

        async def events() -> AsyncIterator[str]:
            started = time.monotonic()
            turn = asyncio.ensure_future(run_in_threadpool(respond, candidate, messages))
            yield completion.chunk({"role": "assistant", "content": ""})
            first_chunk: float | None = None
            pending, spoken, heartbeats = list(fillers), [], 0
            # Mientras corre el turno: frases de espera a su hora y, entre medio, chunks vacíos
            # (heartbeat). Sin nada en `cascade_timeout_seconds`, ElevenLabs reintenta o corta.
            while not turn.done():
                elapsed = time.monotonic() - started
                waits = [pending[0][0] - elapsed] if pending else []
                if settings.heartbeat_seconds > 0:
                    waits.append(settings.heartbeat_seconds)
                timeout = max(0.0, min(waits)) if waits else None
                done, _ = await asyncio.wait({turn}, timeout=timeout)
                if done:
                    break
                if pending and time.monotonic() - started >= pending[0][0]:
                    _, filler = pending.pop(0)
                    # El espacio final evita que el TTS corte la frase (guía de ElevenLabs).
                    yield completion.chunk({"content": filler + " "})
                    spoken.append(filler)
                    first_chunk = first_chunk or time.monotonic()
                elif settings.heartbeat_seconds > 0:
                    yield completion.chunk({"content": ""})
                    heartbeats += 1
            result = turn.result()
            # La respuesta entera en un solo chunk: el TTS la entona como un todo en vez de
            # frase por frase. Sin las frases ya dichas (la de espera, si el turno no llegó).
            reply = " ".join(s for s in _sentences(result.text) if s not in spoken)
            if reply:
                yield completion.chunk({"content": reply + " "})
                first_chunk = first_chunk or time.monotonic()
            if end_call and result.closed:
                yield completion.chunk({"tool_calls": [completion.end_call(index=0)]})
                yield completion.chunk({}, finish_reason="tool_calls")
            else:
                yield completion.chunk({}, finish_reason="stop")
            yield "data: [DONE]\n\n"
            _log_turn(
                candidate.id,
                started,
                first_chunk=first_chunk,
                fillers=len(spoken),
                heartbeats=heartbeats,
            )

        return StreamingResponse(events(), media_type="text/event-stream")

    return router


class _Completion:
    """Respuestas con la forma de OpenAI Chat Completions (lo que espera ElevenLabs)."""

    def __init__(self, model: str) -> None:
        self.id = f"chatcmpl-{uuid.uuid4().hex}"
        self.model = model
        self.created = int(time.time())

    def chunk(self, delta: dict[str, Any], *, finish_reason: str | None = None) -> str:
        payload = {
            "id": self.id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def message(self, text: str, *, end_call: bool) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": text}
        if end_call:
            message["tool_calls"] = [self.end_call()]
        return {
            "id": self.id,
            "object": "chat.completion",
            "created": self.created,
            "model": self.model,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "tool_calls" if end_call else "stop",
                }
            ],
        }

    @staticmethod
    def end_call(index: int | None = None) -> dict[str, Any]:
        call: dict[str, Any] = {
            "id": f"call_{uuid.uuid4().hex[:12]}",
            "type": "function",
            "function": {"name": "end_call", "arguments": "{}"},
        }
        if index is not None:
            call["index"] = index
        return call


def _log_turn(
    candidate_id: str,
    started: float,
    *,
    first_chunk: float | None,
    fillers: int,
    heartbeats: int,
) -> None:
    now = time.monotonic()
    logger.info(
        "voice_llm_turn candidate=%s first_chunk_ms=%s turn_ms=%d fillers=%d heartbeats=%d",
        candidate_id,
        round((first_chunk - started) * 1000) if first_chunk else None,
        round((now - started) * 1000),
        fillers,
        heartbeats,
    )
