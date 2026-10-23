from screening.clock import FixedClock
from screening.domain import Candidate, CandidateStatus, Stage


def test_set_status_records_event_and_updates_timestamp(
    candidate: Candidate, clock: FixedClock
) -> None:
    candidate.set_status(CandidateStatus.CONTACTED, clock.now(), reason="first_contact")

    assert candidate.status == CandidateStatus.CONTACTED
    event = candidate.timeline[-1]
    assert event.type == "status_changed"
    assert event.detail == {
        "previous": CandidateStatus.NEW,
        "new": CandidateStatus.CONTACTED,
        "reason": "first_contact",
    }
    assert candidate.updated_at == clock.now()


def test_set_status_to_same_value_records_nothing(candidate: Candidate, clock: FixedClock) -> None:
    candidate.set_status(CandidateStatus.NEW, clock.now())

    assert candidate.timeline == []


def test_set_stage_records_event(candidate: Candidate, clock: FixedClock) -> None:
    candidate.set_stage(Stage.KNOCKOUT, clock.now())

    assert candidate.stage == Stage.KNOCKOUT
    assert candidate.timeline[-1].type == "stage_changed"


def test_candidate_round_trips_through_json(candidate: Candidate, clock: FixedClock) -> None:
    candidate.set_status(CandidateStatus.IN_SCREENING, clock.now())

    restored = Candidate.model_validate_json(candidate.model_dump_json())

    assert restored == candidate
