"""Métricas de una corrida: accuracy, latencia p50/p95 y tokens y costo del objetivo y del juez."""

from __future__ import annotations

import math
from collections.abc import Iterable

from evals.models import CaseResult, SuiteMetrics, UsageTotals
from screening.ports import LLMUsage


def percentile(values: Iterable[float], pct: float) -> float | None:
    """Percentil con interpolación lineal entre rangos (como `numpy.percentile`)."""
    ordered = sorted(values)
    if not ordered:
        return None
    rank = (len(ordered) - 1) * pct / 100
    low, high = math.floor(rank), math.ceil(rank)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def _totals(usages: Iterable[LLMUsage | None]) -> UsageTotals:
    present = [u for u in usages if u is not None]
    return UsageTotals(
        calls=len(present),
        input_tokens=sum(u.input_tokens for u in present),
        output_tokens=sum(u.output_tokens for u in present),
        cost_usd=sum(u.cost_usd for u in present),
    )


def compute_metrics(results: list[CaseResult]) -> SuiteMetrics:
    total = len(results)
    correct = sum(r.correct for r in results)
    passed = sum(r.verdict == "pass" for r in results)
    latencies = [r.latency_ms for r in results if r.latency_ms is not None]
    target = _totals(r.target_usage for r in results)
    judge = _totals(r.judge_usage for r in results)
    return SuiteMetrics(
        total=total,
        correct=correct,
        passed=passed,
        errors=sum(r.error is not None for r in results),
        accuracy=correct / total if total else 0.0,
        pass_rate=passed / total if total else 0.0,
        latency_p50_ms=percentile(latencies, 50),
        latency_p95_ms=percentile(latencies, 95),
        target=target,
        judge=judge,
        total_cost_usd=target.cost_usd + judge.cost_usd,
    )
