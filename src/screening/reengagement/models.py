"""Resultado de una pasada de calentamiento (spec 011) y del backfill (spec 017)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from screening.domain import ChannelName


class TouchOutcome(StrEnum):
    SENT = "sent"
    FAILED = "failed"
    QUEUED = "queued"
    SKIPPED = "skipped"


class TouchRecord(BaseModel):
    candidate_id: str
    cadence: str
    step: str
    channel: ChannelName
    outcome: TouchOutcome
    error: str | None = None


class ReengagementReport(BaseModel):
    read: int = Field(default=0, description="Fichas devueltas por la consulta de vencidas.")
    capped: bool = Field(
        default=False, description="Cortó por el tope: quedan vencidas para la próxima pasada."
    )
    evaluated: int = Field(default=0, description="Fichas con una cadencia activa.")
    touches: list[TouchRecord] = Field(default_factory=list)
    closed: list[str] = Field(default_factory=list, description="Fichas que pasaron a no_response.")
    errors: list[str] = Field(
        default_factory=list, description="Fichas que no se pudieron procesar."
    )


class BackfillReport(BaseModel):
    read: int = Field(default=0, description="Fichas abiertas revisadas.")
    updated: int = Field(default=0, description="Fichas cuyo next_touch_at cambió.")
