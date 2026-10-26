"""Modelos de dominio compartidos por todo el sistema.

Es el contrato de la ola 0 (ver `specs/000-contratos-base.md`): las features lo usan,
pero no lo modifican sin coordinarlo.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class Country(StrEnum):
    ES = "ES"
    MX = "MX"


class Language(StrEnum):
    ES = "es"
    EN = "en"


class Stage(StrEnum):
    """Etapas de la conversación, en orden."""

    WELCOME = "welcome"
    KNOCKOUT = "knockout"
    PROFILE = "profile"
    CLOSING = "closing"
    DONE = "done"


class CandidateStatus(StrEnum):
    """Estados visibles en el CRM."""

    NEW = "new"
    CONTACTED = "contacted"
    IN_SCREENING = "in_screening"
    QUALIFIED = "qualified"
    DISQUALIFIED = "disqualified"
    NO_RESPONSE = "no_response"
    INVALID_DATA = "invalid_data"
    OPTED_OUT = "opted_out"
    INTERVIEW = "interview"
    HIRED = "hired"


class VehicleType(StrEnum):
    MOTORCYCLE = "motorcycle"
    CAR = "car"


class Availability(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    WEEKENDS = "weekends"


class Schedule(StrEnum):
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"
    FLEXIBLE = "flexible"


class Sentiment(StrEnum):
    NEUTRAL = "neutral"
    FRUSTRATED = "frustrated"
    CONFUSED = "confused"
    RUSHED = "rushed"


class ChannelName(StrEnum):
    WHATSAPP = "whatsapp"
    SMS = "sms"
    CONSOLE = "console"
    WEB = "web"  # chat web público del demo (spec 019)


class MessageRole(StrEnum):
    CANDIDATE = "candidate"
    AGENT = "agent"
    SYSTEM = "system"


class ApplicationPayload(BaseModel):
    """JSON que entrega el portal de empleo al postular: la entrada del sistema.

    Los campos de contacto son opcionales a propósito: si faltan o son inválidos, el
    intake crea igual la ficha con estado `invalid_data` para que HR la revise.
    """

    model_config = ConfigDict(extra="ignore")

    application_id: str
    source: str
    full_name: str | None = None
    phone: str | None = None
    email: str | None = None
    country: Country | None = None
    city: str | None = None
    vacancy_id: str | None = None
    applied_at: datetime | None = None


class DeliveryExperience(BaseModel):
    """Solo experiencia relevante (reparto, mensajería, courier)."""

    years: float = Field(ge=0)
    platforms: list[str] = Field(default_factory=list)


class ScreeningData(BaseModel):
    """Campos que llena la conversación. Todos son opcionales hasta que se recogen."""

    full_name: str | None = None
    consent: bool | None = None
    has_license: bool | None = None
    license_type: str | None = None
    license_years: float | None = Field(default=None, ge=0)
    vehicle_type: VehicleType | None = None
    owns_vehicle: bool | None = None
    city: str | None = None
    availability: Availability | None = None
    preferred_schedule: Schedule | None = None
    delivery_experience: DeliveryExperience | None = None
    start_date: date | None = None
    work_permit: bool | None = None
    needs_review: list[str] = Field(
        default_factory=list,
        description="Campos que no se pudieron validar tras los reintentos.",
    )


class KnockoutResult(BaseModel):
    passed: bool
    failed_rule: str | None = None
    reason_code: str | None = None


class ScoreBreakdown(BaseModel):
    total: float
    components: dict[str, float] = Field(default_factory=dict)


class Message(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    role: MessageRole
    text: str
    channel: ChannelName
    created_at: datetime = Field(default_factory=utc_now)
    external_id: str | None = Field(
        default=None, description="ID del proveedor (p. ej. WhatsApp) para deduplicar."
    )


class TimelineEvent(BaseModel):
    """Cada cambio de la ficha queda registrado para que el CRM lo muestre en vivo."""

    at: datetime
    type: str
    detail: dict[str, Any] = Field(default_factory=dict)


class ReengagementState(BaseModel):
    cadence: str | None = None
    next_step_index: int = 0
    next_touch_at: datetime | None = None
    touches_sent: int = 0


class Candidate(BaseModel):
    """Ficha del candidato: lo que se persiste y lo que lee el CRM."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    application: ApplicationPayload
    phone: str | None = Field(default=None, description="Teléfono normalizado a E.164.")
    country: Country | None = None
    status: CandidateStatus = CandidateStatus.NEW
    stage: Stage = Stage.WELCOME
    data: ScreeningData = Field(default_factory=ScreeningData)
    language: Language = Language.ES
    sentiment: Sentiment = Sentiment.NEUTRAL
    messages: list[Message] = Field(default_factory=list)
    knockout: KnockoutResult | None = None
    score: ScoreBreakdown | None = None
    summary: str | None = None
    human_requested: bool = False
    reengagement: ReengagementState = Field(default_factory=ReengagementState)
    timeline: list[TimelineEvent] = Field(default_factory=list)
    last_candidate_message_at: datetime | None = None
    last_agent_message_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    def record_event(self, at: datetime, type: str, **detail: Any) -> None:
        self.timeline.append(TimelineEvent(at=at, type=type, detail=detail))
        self.updated_at = at

    def set_status(self, status: CandidateStatus, at: datetime, reason: str | None = None) -> None:
        if status == self.status:
            return
        previous = self.status
        self.status = status
        self.record_event(at, "status_changed", previous=previous, new=status, reason=reason)

    def set_stage(self, stage: Stage, at: datetime) -> None:
        if stage == self.stage:
            return
        previous = self.stage
        self.stage = stage
        self.record_event(at, "stage_changed", previous=previous, new=stage)
