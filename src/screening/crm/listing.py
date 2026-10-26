"""Filas del listado del CRM: lo justo para la tabla, filtradas y ordenadas (spec 013)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from pydantic import BaseModel, Field

from screening.domain import (
    Candidate,
    CandidateStatus,
    Country,
    MessageRole,
    Sentiment,
    Stage,
)


class LastMessage(BaseModel):
    role: MessageRole
    text: str
    at: datetime


class CandidateRow(BaseModel):
    id: str
    name: str | None = None
    phone: str | None = None
    country: Country | None = None
    city: str | None = None
    status: CandidateStatus
    stage: Stage
    score: float | None = None
    disqualification_reason: str | None = None
    human_requested: bool = False
    sentiment: Sentiment = Sentiment.NEUTRAL
    needs_review: list[str] = Field(default_factory=list)
    last_message: LastMessage | None = None
    created_at: datetime
    updated_at: datetime


class CandidateRowPage(BaseModel):
    """Una página del listado; `next_cursor` es None en la última (spec 018)."""

    items: list[CandidateRow]
    next_cursor: str | None = None


def to_row(candidate: Candidate) -> CandidateRow:
    last = candidate.messages[-1] if candidate.messages else None
    knockout = candidate.knockout
    return CandidateRow(
        id=candidate.id,
        name=candidate.data.full_name or candidate.application.full_name,
        phone=candidate.phone,
        country=candidate.country,
        city=candidate.data.city or candidate.application.city,
        status=candidate.status,
        stage=candidate.stage,
        score=candidate.score.total if candidate.score else None,
        disqualification_reason=knockout.reason_code if knockout and not knockout.passed else None,
        human_requested=candidate.human_requested,
        sentiment=candidate.sentiment,
        needs_review=list(candidate.data.needs_review),
        last_message=LastMessage(role=last.role, text=last.text, at=last.created_at)
        if last
        else None,
        created_at=candidate.created_at,
        updated_at=candidate.updated_at,
    )


def filter_and_sort(
    candidates: Iterable[Candidate],
    *,
    statuses: Iterable[CandidateStatus] | None = None,
    country: Country | None = None,
    stage: Stage | None = None,
) -> list[Candidate]:
    """Puntaje descendente (sin puntaje al final); a igual puntaje, actividad reciente."""
    wanted = set(statuses) if statuses else None
    selected = [
        c
        for c in candidates
        if (wanted is None or c.status in wanted)
        and (country is None or c.country == country)
        and (stage is None or c.stage == stage)
    ]
    # Dos pasadas estables: primero por fecha, luego por puntaje.
    selected.sort(key=lambda c: c.updated_at, reverse=True)
    selected.sort(key=lambda c: (c.score is None, -(c.score.total if c.score else 0.0)))
    return selected
