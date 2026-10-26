"""Chat web público del demo (spec 019): el mismo orquestador que WhatsApp, otra "boca".

- `POST /chat/start`: simula la postulación del portal (nombre, país, teléfono opcional) y
  devuelve el primer mensaje de Sofía.
- `POST /chat/{id}/messages`: entrega el mensaje del candidato al orquestador (con su lease)
  y devuelve la conversación actualizada.
- `GET /chat/{id}`: la conversación, para retomar.

El `id` de la ficha es un UUID imposible de adivinar: es la llave del chat. No expone datos
de otras fichas ni del panel.
"""

from __future__ import annotations

import logging
import random
import uuid
from datetime import datetime, timedelta
from typing import Literal, Protocol

import phonenumbers
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from screening.channels import CandidateBusyError, InboundBatch, InboundHandler, InboundMessage
from screening.clock import Clock
from screening.domain import Candidate, ChannelName, Country, MessageRole, Stage
from screening.intake import IntakeOutcome
from screening.ports import CandidateRepository

logger = logging.getLogger(__name__)

CHAT_PATH = "/chat"
MAX_MESSAGE_CHARS = 1000
# Prefijos de móviles de prueba válidos por país (se completan al azar).
_TEST_PREFIXES = {Country.ES: ("+346", 8), Country.MX: ("+5255", 8)}


class ApplicationStarter(Protocol):
    def start_application(self, raw: dict) -> object: ...


class StartChatRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=80)
    country: Country
    phone: str | None = Field(default=None, max_length=25)
    city: str | None = Field(default=None, max_length=60)


class SendMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class ChatMessage(BaseModel):
    role: Literal["candidate", "agent"]
    text: str
    at: datetime


class ChatView(BaseModel):
    candidate_id: str
    phone: str | None
    status: str
    stage: str
    done: bool
    messages: list[ChatMessage]


def to_view(candidate: Candidate) -> ChatView:
    messages = [
        ChatMessage(role=m.role.value, text=m.text, at=m.created_at)
        for m in candidate.messages
        if m.role in (MessageRole.CANDIDATE, MessageRole.AGENT)
    ]
    return ChatView(
        candidate_id=candidate.id,
        phone=candidate.phone,
        status=candidate.status.value,
        stage=candidate.stage.value,
        done=candidate.stage == Stage.DONE,
        messages=messages,
    )


def generate_test_phone(country: Country, rng: random.Random | None = None) -> str:
    """Un móvil de prueba válido del país, para quien no quiere poner su número."""
    rng = rng or random.SystemRandom()
    prefix, digits = _TEST_PREFIXES[country]
    for _ in range(50):
        candidate = prefix + "".join(rng.choice("0123456789") for _ in range(digits))
        if phonenumbers.is_valid_number(phonenumbers.parse(candidate)):
            return candidate
    raise RuntimeError(f"no se pudo generar un teléfono de prueba para {country}")


def build_web_chat_router(
    *,
    starter: ApplicationStarter,
    handler: InboundHandler,
    repository: CandidateRepository,
    clock: Clock,
) -> APIRouter:
    router = APIRouter(prefix=CHAT_PATH, tags=["chat web"])

    def load(candidate_id: str) -> Candidate:
        candidate = repository.get(candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="conversación no encontrada")
        return candidate

    # `def` y no `async def`: el orquestador es bloqueante (LLM) y FastAPI lo corre en un hilo.
    @router.post("/start")
    def start(body: StartChatRequest) -> ChatView:
        raw = {
            "application_id": f"web-{uuid.uuid4().hex}",
            "source": "web",
            "full_name": body.full_name.strip(),
            "phone": (body.phone or "").strip() or generate_test_phone(body.country),
            "country": body.country.value,
            "city": body.city,
        }
        try:
            result = starter.start_application(raw)
        except CandidateBusyError as e:
            raise HTTPException(status_code=409, detail="intenta de nuevo en unos segundos") from e
        outcome = getattr(result, "outcome", None)
        candidate_id = getattr(result, "candidate_id", None)
        if outcome == IntakeOutcome.INVALID_DATA:
            raise HTTPException(status_code=422, detail="el teléfono no es válido para ese país")
        if candidate_id is None:
            raise HTTPException(status_code=422, detail="no se pudo crear la postulación")
        return to_view(load(candidate_id))

    @router.get("/{candidate_id}")
    def get(candidate_id: str) -> ChatView:
        return to_view(load(candidate_id))

    @router.post("/{candidate_id}/messages")
    def send(candidate_id: str, body: SendMessageRequest) -> ChatView:
        candidate = load(candidate_id)
        if candidate.stage == Stage.DONE or candidate.phone is None:
            return to_view(candidate)  # conversación cerrada: no se procesa nada más
        now = clock.now()
        message = InboundMessage(
            external_id=f"web-in-{uuid.uuid4().hex}",
            sender=candidate.phone,
            text=body.text.strip(),
            type="text",
            sent_at=now - timedelta(milliseconds=1),
            received_at=now,
            channel=ChannelName.WEB,
        )
        try:
            handler.handle(InboundBatch(sender=candidate.phone, messages=[message]))
        except CandidateBusyError as e:
            raise HTTPException(status_code=409, detail="Sofía está respondiendo, espera") from e
        return to_view(load(candidate_id))

    return router
