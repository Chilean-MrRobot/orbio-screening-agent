"""Interfaces entre el dominio y la infraestructura (contrato de la ola 0).

Las implementaciones viven en `screening.adapters` y se inyectan; los tests usan fakes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel

from screening.domain import Candidate, CandidateStatus, ChannelName

# --- Persistencia -----------------------------------------------------------------


class CandidateRepository(Protocol):
    def get(self, candidate_id: str) -> Candidate | None: ...

    def get_by_phone(self, phone: str) -> Candidate | None: ...

    def save(self, candidate: Candidate) -> None: ...

    def list(self, status: CandidateStatus | None = None) -> list[Candidate]: ...


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
