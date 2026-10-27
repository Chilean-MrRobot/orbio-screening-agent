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
from screening.domain import Candidate

logger = logging.getLogger(__name__)

DISABLED_DETAIL = (
    "la llamada no está habilitada en este despliegue "
    "(faltan ELEVENLABS_API_KEY, ELEVENLABS_AGENT_ID o VOICE_LLM_SECRET)"
)
_CANDIDATE_ID = re.compile(r"candidate_id:\s*([\w-]+)")
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


class ConversationTokens(Protocol):
    def conversation_token(self, agent_id: str) -> str: ...


class CallSettings(BaseModel):
    agent_id: str | None = None
    llm_secret: SecretStr | None = None
    wait_after_seconds: float = 1.5  # 0 desactiva la frase de espera
    busy_wait_seconds: float = 8.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> CallSettings:
        values = {
            "agent_id": env.get("ELEVENLABS_AGENT_ID"),
            "llm_secret": env.get("VOICE_LLM_SECRET"),
            "wait_after_seconds": env.get("VOICE_WAIT_AFTER_SECONDS"),
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


def _sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_END.split(text) if s]


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

    def run_turn(candidate: Candidate, text: str) -> TurnResult:
        """Bloqueante (LLM): corre en un hilo. Si el lease está tomado, espera un poco."""
        if not text:  # ElevenLabs a veces pide respuesta sin texto nuevo: no se procesa nada
            return TurnResult(new_agent_text(candidate), is_closed(candidate))
        deadline = time.monotonic() + settings.busy_wait_seconds
        while True:
            try:
                updated = chat.send(candidate.id, text[:MAX_MESSAGE_CHARS])
                return TurnResult(new_agent_text(updated), is_closed(updated))
            except HTTPException as e:
                if e.status_code != 409:
                    raise
                if time.monotonic() >= deadline:
                    busy = speakable(render_template("voice_busy", candidate, config))
                    return TurnResult(busy, False)
                sleep(0.5)

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
        text = user_text_from(messages)
        tool_names = {(t.get("function") or {}).get("name") for t in body.get("tools") or []}
        end_call = "end_call" in tool_names
        completion = _Completion(str(body.get("model") or "orbio"))

        if not body.get("stream"):
            started = time.monotonic()
            result = await run_in_threadpool(run_turn, candidate, text)
            _log_turn(candidate.id, started, first_chunk=None, waited=False)
            return completion.message(result.text, end_call=end_call and result.closed)

        wait_text = ""
        if settings.wait_after_seconds > 0 and text:
            wait_text = speakable(render_template("voice_wait", candidate, config))

        async def events() -> AsyncIterator[str]:
            started = time.monotonic()
            turn = asyncio.ensure_future(run_in_threadpool(run_turn, candidate, text))
            yield completion.chunk({"role": "assistant", "content": ""})
            first_chunk: float | None = None
            waited = False
            if wait_text:
                done, _ = await asyncio.wait({turn}, timeout=settings.wait_after_seconds)
                if not done:
                    # El espacio final evita que el TTS corte la frase (guía de ElevenLabs).
                    yield completion.chunk({"content": wait_text + " "})
                    first_chunk, waited = time.monotonic(), True
            result = await turn
            for sentence in _sentences(result.text):
                yield completion.chunk({"content": sentence + " "})
                first_chunk = first_chunk or time.monotonic()
            if end_call and result.closed:
                yield completion.chunk({"tool_calls": [completion.end_call(index=0)]})
                yield completion.chunk({}, finish_reason="tool_calls")
            else:
                yield completion.chunk({}, finish_reason="stop")
            yield "data: [DONE]\n\n"
            _log_turn(candidate.id, started, first_chunk=first_chunk, waited=waited)

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
    candidate_id: str, started: float, *, first_chunk: float | None, waited: bool
) -> None:
    now = time.monotonic()
    logger.info(
        "voice_llm_turn candidate=%s first_chunk_ms=%s turn_ms=%d waited=%s",
        candidate_id,
        round((first_chunk - started) * 1000) if first_chunk else None,
        round((now - started) * 1000),
        waited,
    )
