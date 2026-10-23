from __future__ import annotations

import pytest

from evals.metrics import compute_metrics, percentile
from evals.models import CaseResult
from screening.ports import LLMUsage


def result(
    correct: bool = True,
    verdict: str = "pass",
    latency_ms: float | None = 100.0,
    target_usage: LLMUsage | None = None,
    judge_usage: LLMUsage | None = None,
    error: str | None = None,
) -> CaseResult:
    return CaseResult(
        case_id="c",
        input={},
        expected="e",
        expected_verdict="pass",
        answer="a",
        verdict=verdict,
        correct=correct,
        latency_ms=latency_ms,
        target_usage=target_usage,
        judge_usage=judge_usage,
        error=error,
    )


def test_percentile_interpolates_linearly() -> None:
    values = [400.0, 100.0, 300.0, 200.0]

    assert percentile(values, 50) == pytest.approx(250.0)
    assert percentile(values, 95) == pytest.approx(385.0)
    assert percentile([42.0], 95) == 42.0
    assert percentile([], 50) is None


def test_accuracy_and_pass_rate_are_separate() -> None:
    results = [
        result(correct=True, verdict="pass"),
        result(correct=True, verdict="fail"),  # caso negativo bien juzgado
        result(correct=False, verdict="pass"),
        result(correct=False, verdict="fail", error="boom", latency_ms=None),
    ]

    metrics = compute_metrics(results)

    assert (metrics.total, metrics.correct, metrics.passed, metrics.errors) == (4, 2, 2, 1)
    assert metrics.accuracy == pytest.approx(0.5)
    assert metrics.pass_rate == pytest.approx(0.5)


def test_latency_ignores_cases_without_latency() -> None:
    results = [result(latency_ms=v) for v in (100.0, 200.0, 300.0, 400.0)]
    results.append(result(latency_ms=None, error="boom"))

    metrics = compute_metrics(results)

    assert metrics.latency_p50_ms == pytest.approx(250.0)
    assert metrics.latency_p95_ms == pytest.approx(385.0)


def test_target_and_judge_usage_are_totaled_separately() -> None:
    target = LLMUsage("t", 100, 50, 10.0, 0.01)
    judge = LLMUsage("j", 300, 20, 5.0, 0.002)
    results = [
        result(target_usage=target, judge_usage=judge),
        result(target_usage=target, judge_usage=judge),
        result(target_usage=None, judge_usage=None, error="boom"),
    ]

    metrics = compute_metrics(results)

    assert metrics.target.model_dump() == {
        "calls": 2,
        "input_tokens": 200,
        "output_tokens": 100,
        "cost_usd": pytest.approx(0.02),
    }
    assert metrics.judge.calls == 2
    assert metrics.judge.input_tokens == 600
    assert metrics.judge.cost_usd == pytest.approx(0.004)
    assert metrics.total_cost_usd == pytest.approx(0.024)


def test_empty_suite() -> None:
    metrics = compute_metrics([])

    assert metrics.total == 0
    assert metrics.accuracy == 0.0
    assert metrics.latency_p50_ms is None
