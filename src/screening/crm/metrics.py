"""Analítica de las conversaciones: función pura sobre la lista de fichas (spec 013)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timedelta

from pydantic import BaseModel

from screening.conversation.usage import usage_totals
from screening.domain import Candidate, CandidateStatus, MessageRole, Stage

S = CandidateStatus

COMPLETED_STATUSES = frozenset({S.QUALIFIED, S.DISQUALIFIED, S.INTERVIEW, S.HIRED})
QUALIFIED_STATUSES = frozenset({S.QUALIFIED, S.INTERVIEW, S.HIRED})
ABANDONED_STATUSES = frozenset({S.NO_RESPONSE, S.OPTED_OUT})
# Conversaciones abiertas: cuentan como abandono si llevan `stale_after` sin actividad.
OPEN_STATUSES = frozenset({S.CONTACTED, S.IN_SCREENING})
MANUAL_REASON = "manual"
UNKNOWN_COUNTRY = "unknown"


class TokenMetrics(BaseModel):
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_cost_usd: float = 0.0
    avg_cost_per_candidate_usd: float | None = None
    cost_per_qualified_usd: float | None = None


class CrmMetrics(BaseModel):
    total: int
    by_status: dict[CandidateStatus, int]
    by_country: dict[str, int]
    contacted: int
    responded: int
    completed: int
    qualified: int
    response_rate: float | None
    completion_rate: float | None
    qualification_rate: float | None
    drop_off_by_stage: dict[Stage, int]
    disqualification_reasons: dict[str, int]
    avg_minutes_to_qualify: float | None
    reengagement_touches: int
    candidates_reengaged: int
    human_requested: int
    avg_score: float | None
    tokens: TokenMetrics


def compute_metrics(
    candidates: Sequence[Candidate], now: datetime, stale_after: timedelta
) -> CrmMetrics:
    contacted = [c for c in candidates if _has_message(c, MessageRole.AGENT)]
    responded = [c for c in candidates if _has_message(c, MessageRole.CANDIDATE)]
    completed = [c for c in candidates if c.status in COMPLETED_STATUSES]
    qualified = [c for c in candidates if c.status in QUALIFIED_STATUSES]

    statuses = Counter(c.status for c in candidates)
    drop_off = Counter(c.stage for c in candidates if _abandoned(c, now, stale_after))
    reasons = Counter(_disqualification_reason(c) for c in candidates if c.status == S.DISQUALIFIED)
    to_qualify = [m for c in candidates if (m := _minutes_to_qualify(c)) is not None]
    scores = [c.score.total for c in candidates if c.score is not None]

    return CrmMetrics(
        total=len(candidates),
        by_status={status: statuses[status] for status in CandidateStatus},
        by_country=dict(
            Counter(c.country.value if c.country else UNKNOWN_COUNTRY for c in candidates)
        ),
        contacted=len(contacted),
        responded=len(responded),
        completed=len(completed),
        qualified=len(qualified),
        response_rate=_ratio(len(responded), len(contacted)),
        completion_rate=_ratio(len(completed), len(responded)),
        qualification_rate=_ratio(len(qualified), len(completed)),
        drop_off_by_stage={stage: drop_off[stage] for stage in Stage},
        disqualification_reasons=dict(reasons.most_common()),
        avg_minutes_to_qualify=_mean(to_qualify),
        reengagement_touches=sum(c.reengagement.touches_sent for c in candidates),
        candidates_reengaged=sum(1 for c in candidates if c.reengagement.touches_sent > 0),
        human_requested=sum(1 for c in candidates if c.human_requested),
        avg_score=_mean(scores),
        tokens=_token_metrics(candidates, len(qualified)),
    )


def _has_message(candidate: Candidate, role: MessageRole) -> bool:
    return any(m.role == role for m in candidate.messages)


def _abandoned(candidate: Candidate, now: datetime, stale_after: timedelta) -> bool:
    if candidate.status in ABANDONED_STATUSES:
        return True
    return candidate.status in OPEN_STATUSES and now - candidate.updated_at >= stale_after


def _disqualification_reason(candidate: Candidate) -> str:
    knockout = candidate.knockout
    if knockout and not knockout.passed and knockout.reason_code:
        return knockout.reason_code
    return MANUAL_REASON


def _minutes_to_qualify(candidate: Candidate) -> float | None:
    for event in candidate.timeline:
        if event.type == "status_changed" and event.detail.get("new") == S.QUALIFIED:
            return (event.at - candidate.created_at).total_seconds() / 60
    return None


def _token_metrics(candidates: Sequence[Candidate], qualified: int) -> TokenMetrics:
    metrics = TokenMetrics()
    with_usage = 0
    for candidate in candidates:
        totals = usage_totals(candidate)
        if totals.calls == 0:
            continue
        with_usage += 1
        metrics.calls += totals.calls
        metrics.input_tokens += totals.input_tokens
        metrics.output_tokens += totals.output_tokens
        metrics.total_cost_usd += totals.cost_usd
    metrics.avg_cost_per_candidate_usd = _ratio(metrics.total_cost_usd, with_usage)
    metrics.cost_per_qualified_usd = _ratio(metrics.total_cost_usd, qualified)
    return metrics


def _ratio(numerator: float, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None
