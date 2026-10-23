"""Suite `summary` (spec 012) con un LLM falso."""

from __future__ import annotations

import pytest

from evals.dataset import load_suite
from evals.suites import SUITES
from evals.targets import summary
from screening.adapters.fake_llm import FakeLLMClient
from screening.domain import CandidateStatus
from screening.ports import LLMError
from screening.summary import SummaryNotes

NOTES = SummaryNotes(observations=["Preguntó por el pago."])


def test_summary_suite_is_registered_and_covers_the_cases() -> None:
    assert "summary" in SUITES
    cases = load_suite("summary")
    assert len(cases) >= 8
    assert all("question" not in case.input for case in cases)  # el juez debe ver todo el input
    candidates = [summary.summary_candidate(case) for case in cases]
    statuses = {c.status for c in candidates}
    assert statuses == {CandidateStatus.QUALIFIED, CandidateStatus.DISQUALIFIED}
    assert any(c.data.needs_review for c in candidates)
    assert any(c.human_requested for c in candidates)
    assert any(c.data.delivery_experience is None for c in candidates)


def test_summary_target_builds_the_record_and_renders(app_config) -> None:
    case = next(c for c in load_suite("summary") if c.id == "sm-04-needs-review-fecha")
    llm = FakeLLMClient([NOTES])

    output = summary.make_target(llm, app_config)(case)

    assert "Revisar: start_date" in output.answer
    assert "Puntaje 61/100" in output.answer
    assert output.answer.endswith("- Preguntó por el pago.")
    assert output.usage is not None
    assert "field_needs_review" in llm.calls[0].messages[0].content


def test_summary_target_raises_when_the_llm_fails(app_config) -> None:
    case = load_suite("summary")[0]
    target = summary.make_target(FakeLLMClient([LLMError("boom")]), app_config)

    with pytest.raises(RuntimeError, match="boom"):
        target(case)
