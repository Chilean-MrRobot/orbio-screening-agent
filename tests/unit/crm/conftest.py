"""Fichas armadas a mano para probar el CRM sin pasar por el orquestador."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any

from screening.conversation.usage import USAGE_EVENT
from screening.domain import (
    ApplicationPayload,
    Candidate,
    CandidateStatus,
    ChannelName,
    Country,
    KnockoutResult,
    Message,
    MessageRole,
    ScoreBreakdown,
    Stage,
    TimelineEvent,
)

T0 = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)
_ids = count(1)


def make_candidate(
    *,
    status: CandidateStatus = CandidateStatus.NEW,
    stage: Stage = Stage.WELCOME,
    country: Country | None = Country.ES,
    city: str | None = "Madrid",
    name: str | None = "Lucía Pérez",
    score: float | None = None,
    created_at: datetime = T0,
    updated_at: datetime | None = None,
    agent_messages: int = 0,
    candidate_messages: int = 0,
    knockout_reason: str | None = None,
    human_requested: bool = False,
    touches: int = 0,
    usage: list[dict[str, Any]] | None = None,
    events: list[tuple[datetime, str, dict[str, Any]]] | None = None,
) -> Candidate:
    n = next(_ids)
    candidate = Candidate(
        id=f"c{n:03d}",
        application=ApplicationPayload(
            application_id=f"app-{n}", source="portal", full_name=name, city=city
        ),
        phone=f"+34600000{n:03d}",
        country=country,
        status=status,
        stage=stage,
        created_at=created_at,
        updated_at=updated_at or created_at,
        human_requested=human_requested,
    )
    at = created_at
    for i in range(agent_messages):
        at = created_at + timedelta(minutes=2 * i)
        candidate.messages.append(
            Message(
                role=MessageRole.AGENT,
                text=f"agente {i}",
                channel=ChannelName.CONSOLE,
                created_at=at,
            )
        )
    for i in range(candidate_messages):
        at = created_at + timedelta(minutes=2 * i + 1)
        candidate.messages.append(
            Message(
                role=MessageRole.CANDIDATE,
                text=f"candidato {i}",
                channel=ChannelName.WHATSAPP,
                created_at=at,
            )
        )
    candidate.messages.sort(key=lambda m: m.created_at)
    if score is not None:
        candidate.score = ScoreBreakdown(total=score, components={"experience": score})
    if knockout_reason is not None:
        candidate.knockout = KnockoutResult(
            passed=False, failed_rule="rule", reason_code=knockout_reason
        )
    candidate.reengagement.touches_sent = touches
    for item in usage or []:
        candidate.timeline.append(_event(created_at, USAGE_EVENT, item))
    for event_at, type_, detail in events or []:
        candidate.timeline.append(_event(event_at, type_, detail))
    candidate.updated_at = updated_at or created_at
    return candidate


def _event(at: datetime, type_: str, detail: dict[str, Any]) -> TimelineEvent:
    return TimelineEvent(at=at, type=type_, detail=detail)


def usage(input_tokens: int = 100, output_tokens: int = 20, cost: float = 0.001) -> dict[str, Any]:
    return {
        "turn": "extract",
        "model": "gpt-5-mini",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency_ms": 300.0,
        "cost_usd": cost,
    }
