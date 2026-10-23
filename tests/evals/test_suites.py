"""Corre cada suite del golden dataset contra el LLM real: `pytest -m eval`."""

from __future__ import annotations

import os

import pytest

from evals.dataset import load_suite
from evals.judge import Judge
from evals.report import write_report
from evals.runner import run_suite
from evals.suites import SUITES
from screening.adapters.openai_llm import OpenAILLMClient
from screening.clock import SystemClock

pytestmark = [
    pytest.mark.eval,
    pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="falta OPENAI_API_KEY"),
]


@pytest.mark.parametrize("suite", sorted(SUITES))
def test_suite_meets_threshold(suite: str, app_config) -> None:
    llm = OpenAILLMClient.from_env()
    spec = SUITES[suite]

    report = run_suite(
        suite,
        load_suite(suite),
        spec.make_target(llm, app_config),
        Judge(llm, app_config.llm.judge_model),
        clock=SystemClock(),
    )
    _, md_path = write_report(report)

    assert report.metrics.accuracy >= spec.threshold, f"ver {md_path}"
