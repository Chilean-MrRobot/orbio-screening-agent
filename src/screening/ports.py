"""Interfaces entre el dominio y la infraestructura (contrato de la ola 0).

Las implementaciones viven en `screening.adapters` y se inyectan; los tests usan fakes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from screening.domain import (
    Candidate,
    CandidateStatus,
    ChannelName,
    Country,
    Language,
    Message,
    ScreeningData,
    Sentiment,
    Stage,
)

# --- Persistencia -----------------------------------------------------------------


CandidateOrder = Literal["updated_desc", "score_desc", "next_touch_asc"]


class CandidateQuery(BaseModel):
    """Consulta por índice: nada de recorrer todas las fichas (ver CLAUDE.md, Escalabilidad)."""

    statuses: list[CandidateStatus] | None = None
    country: Country | None = None
    stage: Stage | None = None
    touch_due_before: datetime | None = Field(
        default=None, description="Solo fichas con `reengagement.next_touch_at` <= este instante."
    )
    order: CandidateOrder = "updated_desc"
    limit: int = Field(default=50, ge=1, le=500)
    cursor: str | None = Field(default=None, description="`next_cursor` de la página anterior.")


class CandidatePage(BaseModel):
    items: list[Candidate]
    next_cursor: str | None = None


class CandidateRepository(Protocol):
    def get(self, candidate_id: str) -> Candidate | None: ...

    def get_by_phone(self, phone: str) -> Candidate | None: ...

    def save(self, candidate: Candidate) -> None: ...

    def query(self, query: CandidateQuery) -> CandidatePage: ...

    def count_by_status(self) -> dict[CandidateStatus, int]: ...

    def list(self, status: CandidateStatus | None = None) -> list[Candidate]:
        """Recorre todo: solo para tests, el seed del demo y scripts. El código usa `query`."""
        ...


class LeaseStore(Protocol):
    """Exclusión entre instancias (p. ej. un candidato a la vez), con vencimiento.

    `try_acquire` es atómico: devuelve True si `owner` obtuvo o renovó el lease, o si el
    anterior venció. Un proceso caído no bloquea para siempre: el lease expira en `ttl`.
    """

    def try_acquire(self, key: str, owner: str, *, now: datetime, ttl: timedelta) -> bool: ...

    def release(self, key: str, owner: str) -> None: ...


# --- LLM --------------------------------------------------------------------------


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class LLMUsage:
    """Lo que necesitan las métricas de evaluación: tokens, latencia y costo."""

    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cost_usd: float


@dataclass(frozen=True)
class TextResult:
    text: str
    usage: LLMUsage


@dataclass(frozen=True)
class StructuredResult[T: BaseModel]:
    output: T
    usage: LLMUsage


class LLMError(Exception):
    """Falla del proveedor o salida que no cumple el esquema tras los reintentos."""


class LLMClient(Protocol):
    def generate_text(
        self,
        *,
        system: str,
        messages: list[ChatMessage],
        model: str,
        temperature: float | None = None,
    ) -> TextResult: ...

    def generate_structured[T: BaseModel](
        self,
        *,
        system: str,
        messages: list[ChatMessage],
        schema: type[T],
        model: str,
        temperature: float | None = None,
    ) -> StructuredResult[T]: ...


# --- Canales ----------------------------------------------------------------------


@dataclass(frozen=True)
class OutboundMessage:
    to: str
    text: str
    template: str | None = None
    template_params: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SendResult:
    ok: bool
    external_id: str | None = None
    error: str | None = None


class MessageChannel(Protocol):
    name: ChannelName

    def send(self, message: OutboundMessage) -> SendResult: ...


# --- Base de conocimiento (ola 2, spec 010) ---------------------------------------


@dataclass(frozen=True)
class KnowledgeSnippet:
    source: str
    text: str
    score: float


class KnowledgeBase(Protocol):
    def search(self, query: str, *, limit: int = 3) -> list[KnowledgeSnippet]: ...


# --- Agente conversacional (ola 2, spec 009) --------------------------------------
# Independiente del framework: la implementación usa Google ADK, el orquestador (008)
# solo conoce esta interfaz. Dos turnos separados: extraer datos y generar la respuesta.


class ExtractionOutcome(BaseModel):
    """Lo que el turno de extracción entendió de los mensajes nuevos del candidato."""

    updates: ScreeningData = Field(
        default_factory=ScreeningData,
        description="Solo los campos que el candidato entregó o corrigió en estos mensajes.",
    )
    provided_fields: list[str] = Field(
        default_factory=list, description="Nombres de los campos presentes en `updates`."
    )
    invalid_fields: list[str] = Field(
        default_factory=list,
        description="Campos que intentó responder pero de forma inválida o ambigua.",
    )
    language: Language = Language.ES
    sentiment: Sentiment = Sentiment.NEUTRAL
    opt_out: bool = False
    human_requested: bool = False
    question: str | None = Field(
        default=None, description="Pregunta del candidato para responder con la FAQ."
    )
    usage: list[LLMUsage] = Field(default_factory=list)


class ReplyRequest(BaseModel):
    """Qué necesita decir el agente en este turno; lo decide el orquestador, no el LLM."""

    candidate: Candidate
    stage: Stage
    ask_fields: list[str] = Field(
        default_factory=list, description="Campos que faltan, en el orden a preguntar."
    )
    retry_fields: list[str] = Field(
        default_factory=list, description="Campos a repreguntar con un ejemplo."
    )
    question: str | None = None
    closing_summary: bool = Field(
        default=False, description="Etapa de cierre: resumir los datos y pedir confirmación."
    )


class ReplyOutcome(BaseModel):
    text: str
    usage: list[LLMUsage] = Field(default_factory=list)


class ScreeningAgent(Protocol):
    def extract(
        self,
        candidate: Candidate,
        new_messages: list[Message],
        expected_fields: list[str],
    ) -> ExtractionOutcome: ...

    def reply(self, request: ReplyRequest) -> ReplyOutcome: ...
