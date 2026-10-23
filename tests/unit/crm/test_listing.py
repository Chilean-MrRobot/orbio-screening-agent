from __future__ import annotations

from datetime import timedelta

from screening.crm.listing import filter_and_sort, to_row
from screening.domain import CandidateStatus, Country, MessageRole, Stage
from tests.unit.crm.conftest import T0, make_candidate


def test_row_takes_the_last_message_and_extracted_data() -> None:
    candidate = make_candidate(
        status=CandidateStatus.QUALIFIED,
        score=82,
        agent_messages=2,
        candidate_messages=2,
        human_requested=True,
    )
    candidate.data.full_name = "Lucía Pérez Gómez"
    candidate.data.city = "Valencia"
    candidate.data.needs_review = ["start_date"]

    row = to_row(candidate)

    assert row.id == candidate.id
    assert row.name == "Lucía Pérez Gómez"
    assert row.city == "Valencia"
    assert row.score == 82
    assert row.human_requested is True
    assert row.needs_review == ["start_date"]
    assert row.last_message is not None
    assert row.last_message.role == MessageRole.CANDIDATE
    assert row.last_message.text == "candidato 1"


def test_row_falls_back_to_the_application() -> None:
    candidate = make_candidate(name="Ana López", city="CDMX")

    row = to_row(candidate)

    assert row.name == "Ana López"
    assert row.city == "CDMX"
    assert row.last_message is None
    assert row.score is None


def test_row_shows_the_disqualification_reason() -> None:
    candidate = make_candidate(status=CandidateStatus.DISQUALIFIED, knockout_reason="no_license")

    assert to_row(candidate).disqualification_reason == "no_license"


def test_sorts_by_score_then_recent_activity() -> None:
    low = make_candidate(score=40, updated_at=T0 + timedelta(hours=5))
    high = make_candidate(score=90, updated_at=T0)
    no_score_old = make_candidate(updated_at=T0)
    no_score_new = make_candidate(updated_at=T0 + timedelta(hours=1))
    tie_old = make_candidate(score=40, updated_at=T0)

    result = filter_and_sort([no_score_old, low, tie_old, no_score_new, high])

    assert [c.id for c in result] == [
        high.id,
        low.id,
        tie_old.id,
        no_score_new.id,
        no_score_old.id,
    ]


def test_filters_by_status_country_and_stage() -> None:
    mx_qualified = make_candidate(
        status=CandidateStatus.QUALIFIED, country=Country.MX, stage=Stage.DONE
    )
    mx_interview = make_candidate(
        status=CandidateStatus.INTERVIEW, country=Country.MX, stage=Stage.DONE
    )
    es_qualified = make_candidate(status=CandidateStatus.QUALIFIED, stage=Stage.DONE)
    mx_screening = make_candidate(
        status=CandidateStatus.IN_SCREENING, country=Country.MX, stage=Stage.PROFILE
    )
    all_ = [mx_qualified, mx_interview, es_qualified, mx_screening]

    by_status = filter_and_sort(
        all_,
        statuses=[CandidateStatus.QUALIFIED, CandidateStatus.INTERVIEW],
        country=Country.MX,
    )
    by_stage = filter_and_sort(all_, stage=Stage.PROFILE)

    assert {c.id for c in by_status} == {mx_qualified.id, mx_interview.id}
    assert [c.id for c in by_stage] == [mx_screening.id]
