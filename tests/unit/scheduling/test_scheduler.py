"""`InterviewScheduler`: opciones, reserva, reagendar y cancelar (spec 021)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from screening.adapters.memory import InMemoryInterviewSlotStore
from screening.clock import FixedClock
from screening.config import AppConfig
from screening.domain import (
    ApplicationPayload,
    Candidate,
    CandidateStatus,
    Country,
    InterviewModality,
    InterviewSlot,
    Stage,
)
from screening.scheduling import InterviewScheduler, awaiting_choice, mark_manual, slot_id


@pytest.fixture
def store() -> InMemoryInterviewSlotStore:
    return InMemoryInterviewSlotStore()


@pytest.fixture
def scheduler(
    app_config: AppConfig, store: InMemoryInterviewSlotStore, clock: FixedClock
) -> InterviewScheduler:
    return InterviewScheduler(app_config, store, clock)


def make(country: Country = Country.ES, city: str = "Madrid", id: str = "c1") -> Candidate:
    application = ApplicationPayload(
        application_id=id, source="test", full_name="Ana Ruiz", country=country, city=city
    )
    return Candidate(id=id, application=application, country=country, phone="+34600000001")


def test_options_are_three_free_slots_on_different_days(
    scheduler: InterviewScheduler, store: InMemoryInterviewSlotStore
) -> None:
    first = scheduler.free_slots(Country.ES)[0]
    store.try_reserve(slot_id("ES", first.start), "otro", start=first.start, now=first.start)

    options = scheduler.options(make())

    assert len(options) == 3
    assert first not in options  # el reservado no se ofrece
    assert len({o.start.date() for o in options}) == 3


def test_options_skip_declined_slots(scheduler: InterviewScheduler) -> None:
    candidate = make()
    first = scheduler.options(candidate)
    candidate.scheduling.declined = list(first)

    second = scheduler.options(candidate)

    assert len(second) == 3
    assert {s.start for s in second}.isdisjoint(s.start for s in first)


def test_book_reserves_and_fills_the_interview(
    scheduler: InterviewScheduler, store: InMemoryInterviewSlotStore, clock: FixedClock
) -> None:
    candidate = make()
    slot = scheduler.options(candidate)[1]

    interview = scheduler.book(candidate, slot, booked_by="agent")

    assert interview is not None and candidate.interview == interview
    assert interview.modality == InterviewModality.VIDEO and interview.location is None
    assert interview.timezone == "Europe/Madrid" and interview.booked_at == clock.now()
    assert store.owner(interview.slot_id) == candidate.id


def test_two_candidates_cannot_book_the_same_slot(scheduler: InterviewScheduler) -> None:
    slot = scheduler.free_slots(Country.ES)[0]

    assert scheduler.book(make(id="a"), slot, booked_by="agent") is not None
    loser = make(id="b")
    assert scheduler.book(loser, slot, booked_by="agent") is None
    assert loser.interview is None
    assert slot not in scheduler.free_slots(Country.ES)


def test_a_slot_that_is_no_longer_valid_cannot_be_booked(
    scheduler: InterviewScheduler, clock: FixedClock
) -> None:
    slot = scheduler.free_slots(Country.ES)[0]
    clock.advance(hours=2)  # ahora está a menos de 24 h

    assert scheduler.book(make(), slot, booked_by="agent") is None
    off_hours = InterviewSlot(
        start=datetime(2026, 10, 27, 3, 0, tzinfo=UTC),
        end=datetime(2026, 10, 27, 3, 30, tzinfo=UTC),
    )
    assert scheduler.book(make(), off_hours, booked_by="agent") is None


def test_rescheduling_releases_the_previous_slot(
    scheduler: InterviewScheduler, store: InMemoryInterviewSlotStore
) -> None:
    candidate = make()
    first, second = scheduler.free_slots(Country.ES)[:2]
    old = scheduler.book(candidate, first, booked_by="agent")
    assert old is not None

    new = scheduler.book(candidate, second, booked_by="Marta")

    assert new is not None and new.booked_by == "Marta"
    assert store.owner(old.slot_id) is None
    assert store.owner(new.slot_id) == candidate.id


def test_cancel_releases_the_slot(
    scheduler: InterviewScheduler, store: InMemoryInterviewSlotStore
) -> None:
    candidate = make()
    interview = scheduler.book(candidate, scheduler.free_slots(Country.ES)[0], booked_by="agent")
    assert interview is not None

    assert scheduler.cancel(candidate) == interview
    assert candidate.interview is None and store.owner(interview.slot_id) is None
    assert scheduler.cancel(candidate) is None


def test_in_person_interviews_use_the_city_location_with_aliases(
    scheduler: InterviewScheduler,
) -> None:
    candidate = make(Country.MX, city="CDMX")

    interview = scheduler.book(candidate, scheduler.options(candidate)[0], booked_by="agent")

    assert interview is not None
    assert interview.modality == InterviewModality.IN_PERSON
    assert interview.location == "Av. Insurgentes Sur 1235, col. Del Valle"
    assert interview.timezone == "America/Mexico_City"
    assert scheduler.city(candidate) == "Ciudad de México"


def test_disabled_scheduling_offers_nothing(
    app_config: AppConfig, store: InMemoryInterviewSlotStore, clock: FixedClock
) -> None:
    assert app_config.scheduling is not None
    off = app_config.model_copy(
        update={"scheduling": app_config.scheduling.model_copy(update={"enabled": False})}
    )
    scheduler = InterviewScheduler(off, store, clock)

    assert not scheduler.enabled_for(make())
    assert scheduler.options(make()) == []
    no_country = make()
    no_country.country = None
    assert not InterviewScheduler(app_config, store, clock).enabled_for(no_country)


def test_mark_manual_ends_the_conversation_but_keeps_qualified(clock: FixedClock) -> None:
    candidate = make()
    candidate.status = CandidateStatus.QUALIFIED
    candidate.stage = Stage.SCHEDULING
    assert awaiting_choice(candidate)

    mark_manual(candidate, "no_response", clock.now() + timedelta(days=5))

    assert candidate.stage == Stage.DONE and candidate.status == CandidateStatus.QUALIFIED
    assert candidate.scheduling.manual_required
    assert candidate.scheduling.manual_reason == "no_response"
    assert not awaiting_choice(candidate)
    assert candidate.timeline[-1].type == "interview_manual_required"
