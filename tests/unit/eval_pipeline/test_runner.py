from __future__ import annotations

import pytest

from evals.judge import Judge
from evals.models import EvalCase, TargetOutput
from evals.runner import run_suite
from screening.adapters.fake_llm import FakeLLMClient
from screening.clock import FixedClock
from screening.ports import LLMError, LLMUsage
from tests.unit.eval_pipeline.helpers import make_case

TARGET_USAGE = LLMUsage("target-model", 50, 30, 999.0, 0.005)


class StepTimer:
    """Cada llamada al objetivo dura 100 ms."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 0.1
        return self.now


def answer_with(text: str):
    def target(case: EvalCase) -> TargetOutput:
        return TargetOutput(answer=text, usage=TARGET_USAGE)

    return target


def run(cases, target, judge_llm, clock):
    return run_suite(
        "demo", cases, target, Judge(judge_llm, "judge-model"), clock=clock, timer=StepTimer()
    )


def test_pass_verdict_is_correct_when_pass_is_expected(judge_llm, clock) -> None:
    report = run([make_case()], answer_with("Sí, claro"), judge_llm, clock)

    case = report.cases[0]
    assert (case.verdict, case.score, case.correct) == ("pass", 5, True)
    assert case.reasoning == "motivo"
    assert case.answer == "Sí, claro"
    assert report.metrics.accuracy == 1.0


def test_negative_case_is_correct_when_judge_fails_it(judge_llm, clock) -> None:
    cases = [make_case("pos", "pass"), make_case("neg", "fail")]

    report = run(cases, answer_with("No"), judge_llm, clock)

    assert [c.correct for c in report.cases] == [False, True]
    assert report.metrics.accuracy == pytest.approx(0.5)
    assert report.metrics.pass_rate == 0.0


def test_target_error_is_recorded_and_suite_continues(judge_llm, clock) -> None:
    def target(case: EvalCase) -> TargetOutput:
        if case.id == "boom":
            raise RuntimeError("se cayó")
        return TargetOutput(answer="Sí")

    report = run([make_case("boom"), make_case("ok")], target, judge_llm, clock)

    failed, ok = report.cases
    assert failed.error == "RuntimeError: se cayó"
    assert (failed.verdict, failed.correct, failed.latency_ms) == ("fail", False, None)
    assert ok.correct is True
    assert len(judge_llm.calls) == 1
    assert report.metrics.errors == 1


def test_judge_error_is_recorded(clock) -> None:
    judge_llm = FakeLLMClient([LLMError("juez caído")])

    report = run([make_case()], answer_with("Sí"), judge_llm, clock)

    case = report.cases[0]
    assert case.error == "juez: juez caído"
    assert case.correct is False
    assert case.latency_ms == pytest.approx(100.0)
    assert case.target_usage == TARGET_USAGE


def test_latency_is_wall_clock_of_target_and_usage_is_kept(judge_llm, clock) -> None:
    report = run([make_case()], answer_with("Sí"), judge_llm, clock)

    case = report.cases[0]
    assert case.latency_ms == pytest.approx(100.0)
    assert case.target_usage == TARGET_USAGE
    assert case.judge_usage == LLMUsage("judge-model", 100, 20, 1.0, 0.001)
    assert report.metrics.target.cost_usd == pytest.approx(0.005)
    assert report.metrics.judge.cost_usd == pytest.approx(0.001)


def test_report_metadata(judge_llm, clock: FixedClock) -> None:
    report = run([make_case()], answer_with("Sí"), judge_llm, clock)

    assert report.suite == "demo"
    assert report.started_at == clock.now()
    assert report.judge_model == "judge-model"
    assert report.judge_prompt_version.startswith("v")


def test_on_result_is_called_per_case(judge_llm, clock) -> None:
    seen: list[str] = []

    run_suite(
        "demo",
        [make_case("a"), make_case("b")],
        answer_with("Sí"),
        Judge(judge_llm, "m"),
        clock=clock,
        on_result=lambda r: seen.append(r.case_id),
    )

    assert seen == ["a", "b"]
