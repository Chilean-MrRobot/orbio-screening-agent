from __future__ import annotations

from pathlib import Path

from evals.judge import Judge
from evals.models import SuiteReport, TargetOutput
from evals.report import render_markdown, write_report
from evals.runner import run_suite
from screening.ports import LLMUsage
from tests.unit.eval_pipeline.helpers import make_case


def make_report(judge_llm, clock) -> SuiteReport:
    def target(case):
        text = "Sí, claro" if case.id == "ok" else "No, nunca"
        return TargetOutput(answer=text, usage=LLMUsage("t", 10, 5, 1.0, 0.0001))

    return run_suite(
        "demo",
        [make_case("ok"), make_case("mal")],
        target,
        Judge(judge_llm, "judge-model"),
        clock=clock,
    )


def test_write_report_creates_json_and_markdown(tmp_path: Path, judge_llm, clock) -> None:
    report = make_report(judge_llm, clock)

    json_path, md_path = write_report(report, tmp_path / "results")

    assert json_path.name == "demo-20261022T100000Z.json"
    assert md_path.name == "demo-20261022T100000Z.md"
    assert SuiteReport.model_validate_json(json_path.read_text("utf-8")) == report


def test_markdown_has_summary_and_incorrect_cases(judge_llm, clock) -> None:
    md = render_markdown(make_report(judge_llm, clock))

    assert "# Evaluación: demo" in md
    assert "Accuracy" in md and "50.0%" in md
    assert "p50" in md and "p95" in md
    assert "judge-model" in md
    assert "`mal`" in md
    assert "No, nunca" in md
    assert "`ok`" not in md.split("## Casos incorrectos")[1]
