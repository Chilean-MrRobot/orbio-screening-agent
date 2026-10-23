from __future__ import annotations

import pytest
from pydantic import ValidationError

from screening.crm.transitions import (
    StatusChangeRequest,
    TransitionError,
    allowed_targets,
    apply_status_change,
)
from screening.domain import CandidateStatus, Stage
from tests.unit.crm.conftest import T0, make_candidate

S = CandidateStatus


@pytest.mark.parametrize(
    ("status", "targets"),
    [
        (S.QUALIFIED, [S.INTERVIEW, S.DISQUALIFIED]),
        (S.IN_SCREENING, [S.INTERVIEW, S.DISQUALIFIED]),
        (S.INTERVIEW, [S.HIRED, S.DISQUALIFIED]),
        (S.NEW, [S.DISQUALIFIED]),
        (S.HIRED, []),
        (S.DISQUALIFIED, []),
        (S.OPTED_OUT, []),
    ],
)
def test_allowed_targets(status: CandidateStatus, targets: list[CandidateStatus]) -> None:
    assert allowed_targets(status) == targets


def test_move_to_interview_records_the_recruiter_action() -> None:
    candidate = make_candidate(status=S.QUALIFIED, stage=Stage.DONE)

    apply_status_change(candidate, StatusChangeRequest(status="interview", recruiter="Marta"), T0)

    assert candidate.status == S.INTERVIEW
    assert candidate.updated_at == T0
    types = [e.type for e in candidate.timeline]
    assert types == ["status_changed", "recruiter_action"]
    action = candidate.timeline[-1].detail
    assert action == {"status": "interview", "reason": None, "recruiter": "Marta"}


def test_manual_discard_closes_the_conversation() -> None:
    candidate = make_candidate(status=S.IN_SCREENING, stage=Stage.PROFILE)

    apply_status_change(
        candidate, StatusChangeRequest(status="disqualified", reason="Duplicado"), T0
    )

    assert candidate.status == S.DISQUALIFIED
    assert candidate.stage == Stage.DONE
    changed = next(e for e in candidate.timeline if e.type == "status_changed")
    assert changed.detail["reason"] == "Duplicado"


def test_invalid_transition_raises_and_leaves_the_candidate_untouched() -> None:
    candidate = make_candidate(status=S.QUALIFIED, stage=Stage.DONE)

    with pytest.raises(TransitionError):
        apply_status_change(candidate, StatusChangeRequest(status="hired"), T0)

    assert candidate.status == S.QUALIFIED
    assert candidate.timeline == []


@pytest.mark.parametrize(
    "body",
    [
        {"status": "disqualified"},
        {"status": "disqualified", "reason": "   "},
        {"status": "qualified"},
    ],
)
def test_request_validation(body: dict) -> None:
    with pytest.raises(ValidationError):
        StatusChangeRequest.model_validate(body)
