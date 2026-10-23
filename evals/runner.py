"""Ejecuta una suite: llama al objetivo por caso, pide el veredicto al juez y calcula métricas."""

from __future__ import annotations

import time
from collections.abc import Callable

from evals.judge import Judge
from evals.metrics import compute_metrics
from evals.models import CaseResult, EvalCase, SuiteReport, Target
from screening.clock import Clock


def run_suite(
    suite: str,
    cases: list[EvalCase],
    target: Target,
    judge: Judge,
    *,
    clock: Clock,
    timer: Callable[[], float] = time.perf_counter,
    on_result: Callable[[CaseResult], None] | None = None,
) -> SuiteReport:
    """Corre los casos en orden. Un caso que falla queda con `error` y la suite sigue."""
    started_at = clock.now()
    results: list[CaseResult] = []
    for case in cases:
        result = _run_case(case, target, judge, timer)
        results.append(result)
        if on_result:
            on_result(result)
    return SuiteReport(
        suite=suite,
        started_at=started_at,
        finished_at=clock.now(),
        judge_model=judge.model,
        judge_prompt_version=judge.prompt_version,
        metrics=compute_metrics(results),
        cases=results,
    )


def _run_case(
    case: EvalCase, target: Target, judge: Judge, timer: Callable[[], float]
) -> CaseResult:
    base = {
        "case_id": case.id,
        "input": case.input,
        "expected": case.expected,
        "expected_verdict": case.expected_verdict,
        "metadata": case.metadata,
    }

    start = timer()
    try:
        output = target(case)
    except Exception as e:
        return CaseResult(
            **base,
            answer=None,
            verdict="fail",
            correct=False,
            latency_ms=None,
            error=f"{type(e).__name__}: {e}",
        )
    latency_ms = (timer() - start) * 1000

    try:
        verdict, judge_usage = judge.judge(case, output.answer)
    except Exception as e:
        return CaseResult(
            **base,
            answer=output.answer,
            verdict="fail",
            correct=False,
            latency_ms=latency_ms,
            target_usage=output.usage,
            error=f"juez: {e}",
        )

    return CaseResult(
        **base,
        answer=output.answer,
        verdict=verdict.verdict,
        score=verdict.score,
        reasoning=verdict.reasoning,
        correct=verdict.verdict == case.expected_verdict,
        latency_ms=latency_ms,
        target_usage=output.usage,
        judge_usage=judge_usage,
    )
