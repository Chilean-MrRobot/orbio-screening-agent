"""Uso de tokens por ficha: cada `LLMUsage` queda como evento `llm_usage` del `timeline`."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from screening.domain import Candidate
from screening.ports import LLMUsage

USAGE_EVENT = "llm_usage"


class UsageTotals(BaseModel):
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


def record_usage(candidate: Candidate, at: datetime, turn: str, usage: list[LLMUsage]) -> None:
    for item in usage:
        candidate.record_event(
            at,
            USAGE_EVENT,
            turn=turn,
            model=item.model,
            input_tokens=item.input_tokens,
            output_tokens=item.output_tokens,
            latency_ms=item.latency_ms,
            cost_usd=item.cost_usd,
        )


def usage_totals(candidate: Candidate) -> UsageTotals:
    totals = UsageTotals()
    for event in candidate.timeline:
        if event.type != USAGE_EVENT:
            continue
        totals.calls += 1
        totals.input_tokens += int(event.detail.get("input_tokens", 0))
        totals.output_tokens += int(event.detail.get("output_tokens", 0))
        totals.cost_usd += float(event.detail.get("cost_usd", 0.0))
    return totals
