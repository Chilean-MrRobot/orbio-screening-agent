"""La entrevista en el panel: tabla, ficha, API y cliente (spec 021)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from screening.adapters.memory import InMemoryCandidateRepository, InMemoryInterviewSlotStore
from screening.api.crm import build_crm_router
from screening.clock import FixedClock
from screening.config import AppConfig
from screening.crm import views
from screening.crm.client import CrmClient
from screening.crm.listing import to_row
from screening.domain import Candidate, CandidateStatus, Country, Stage
from screening.scheduling import InterviewScheduler
from tests.unit.crm.conftest import T0, make_candidate

PASSWORD = "s3cret"
AUTH = {"Authorization": f"Bearer {PASSWORD}"}
MONDAY_10 = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)  # lunes 26 a las 10:00 en Madrid


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(T0)


@pytest.fixture
def store() -> InMemoryInterviewSlotStore:
    return InMemoryInterviewSlotStore()


@pytest.fixture
def scheduler(
    app_config: AppConfig, store: InMemoryInterviewSlotStore, clock: FixedClock
) -> InterviewScheduler:
    return InterviewScheduler(app_config, store, clock)


@pytest.fixture
def api(
    repository: InMemoryCandidateRepository, clock: FixedClock, scheduler: InterviewScheduler
) -> TestClient:
    app = FastAPI()
    app.include_router(build_crm_router(repository, clock, PASSWORD, scheduler=scheduler))
    return TestClient(app)


def booked(scheduler: InterviewScheduler, **kwargs) -> Candidate:
    candidate = make_candidate(status=CandidateStatus.INTERVIEW, stage=Stage.DONE, **kwargs)
    slot = next(s for s in scheduler.free_slots(Country.ES) if s.start == MONDAY_10)
    assert scheduler.book(candidate, slot, booked_by="agent") is not None
    return candidate


def qualified(repository: InMemoryCandidateRepository, **kwargs) -> Candidate:
    candidate = make_candidate(status=CandidateStatus.QUALIFIED, stage=Stage.DONE, **kwargs)
    candidate.scheduling.manual_required = True
    candidate.scheduling.manual_reason = "declined_options"
    repository.save(candidate)
    return candidate


# --- Vistas ------------------------------------------------------------------------


def test_table_shows_the_interview_in_local_time(scheduler: InterviewScheduler) -> None:
    (record,) = views.table_records([to_row(booked(scheduler))])

    assert record["Entrevista"] == "lun 26/10 10:00 · Videollamada"


def test_table_flags_manual_scheduling(repository: InMemoryCandidateRepository) -> None:
    rows = [to_row(qualified(repository)), to_row(make_candidate())]

    assert [r["Entrevista"] for r in views.table_records(rows)] == ["📅 Agendar a mano", ""]


def test_detail_lists_date_modality_and_place(scheduler: InterviewScheduler) -> None:
    candidate = make_candidate(
        status=CandidateStatus.QUALIFIED, country=Country.MX, city="Guadalajara"
    )
    slot = scheduler.options(candidate)[0]
    assert scheduler.book(candidate, slot, booked_by="Marta") is not None

    rows = {r["Campo"]: r["Valor"] for r in views.interview_details(candidate)}

    assert rows == {
        "Fecha y hora": "viernes 23 de octubre, 10:00",
        "Zona horaria": "America/Mexico_City",
        "Modalidad": "Presencial",
        "Lugar": "Av. Chapultepec 480, col. Americana",
        "Agendada por": "Marta",
    }
    assert views.manual_scheduling_note(candidate) is None


def test_manual_note_explains_the_reason(repository: InMemoryCandidateRepository) -> None:
    candidate = qualified(repository)

    assert views.interview_details(candidate) == []
    assert views.manual_scheduling_note(candidate) == (
        "📅 Agendar la entrevista a mano: Ninguno de los horarios ofrecidos le sirvió."
    )


def test_scheduling_stage_and_events_have_labels() -> None:
    assert views.STAGE_LABELS[Stage.SCHEDULING] == "Agendamiento"
    assert views.EVENT_LABELS["interview_booked"] == "Entrevista agendada"


# --- API ---------------------------------------------------------------------------


def test_free_slots_come_with_local_labels(
    api: TestClient, repository: InMemoryCandidateRepository
) -> None:
    candidate = qualified(repository)

    response = api.get(f"/crm/candidates/{candidate.id}/slots", params={"limit": 3}, headers=AUTH)

    assert response.status_code == 200
    slots = response.json()
    assert [s["label"] for s in slots] == [
        "viernes 23 de octubre, 12:00",
        "viernes 23 de octubre, 12:30",
        "viernes 23 de octubre, 16:00",
    ]
    assert slots[0]["timezone"] == "Europe/Madrid"


def test_recruiter_books_a_manual_candidate(
    api: TestClient, repository: InMemoryCandidateRepository, store: InMemoryInterviewSlotStore
) -> None:
    candidate = qualified(repository)

    response = api.post(
        f"/crm/candidates/{candidate.id}/interview",
        json={"action": "book", "start": MONDAY_10.isoformat(), "recruiter": "Marta"},
        headers=AUTH,
    )

    assert response.status_code == 200
    stored = repository.get(candidate.id)
    assert stored is not None and stored.interview is not None
    assert stored.status == CandidateStatus.INTERVIEW
    assert stored.interview.booked_by == "Marta"
    assert not stored.scheduling.manual_required
    assert store.owner(stored.interview.slot_id) == candidate.id
    assert stored.timeline[-2].type == "interview_booked"


def test_recruiter_reschedules_and_frees_the_old_slot(
    api: TestClient,
    repository: InMemoryCandidateRepository,
    scheduler: InterviewScheduler,
    store: InMemoryInterviewSlotStore,
) -> None:
    candidate = booked(scheduler)
    old = candidate.interview
    assert old is not None
    repository.save(candidate)
    new_start = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)  # martes 16:00 en Madrid

    response = api.post(
        f"/crm/candidates/{candidate.id}/interview",
        json={"action": "book", "start": new_start.isoformat(), "recruiter": "Marta"},
        headers=AUTH,
    )

    assert response.status_code == 200
    stored = repository.get(candidate.id)
    assert stored is not None and stored.interview is not None
    assert stored.interview.start == new_start
    assert store.owner(old.slot_id) is None
    rescheduled = [e for e in stored.timeline if e.type == "interview_rescheduled"]
    assert rescheduled[0].detail["previous_start"] == MONDAY_10.isoformat()


def test_recruiter_cancels_the_interview(
    api: TestClient,
    repository: InMemoryCandidateRepository,
    scheduler: InterviewScheduler,
    store: InMemoryInterviewSlotStore,
) -> None:
    candidate = booked(scheduler)
    slot_id = candidate.interview.slot_id if candidate.interview else ""
    repository.save(candidate)

    response = api.post(
        f"/crm/candidates/{candidate.id}/interview",
        json={"action": "cancel", "recruiter": "Marta"},
        headers=AUTH,
    )

    assert response.status_code == 200
    stored = repository.get(candidate.id)
    assert stored is not None
    assert stored.interview is None and stored.status == CandidateStatus.QUALIFIED
    assert stored.scheduling.manual_reason == "cancelled"
    assert store.owner(slot_id) is None


def test_a_taken_slot_is_a_conflict(
    api: TestClient, repository: InMemoryCandidateRepository, scheduler: InterviewScheduler
) -> None:
    booked(scheduler)  # otro candidato tiene el lunes a las 10
    candidate = qualified(repository)

    response = api.post(
        f"/crm/candidates/{candidate.id}/interview",
        json={"action": "book", "start": MONDAY_10.isoformat()},
        headers=AUTH,
    )

    assert response.status_code == 409
    assert "no está disponible" in response.json()["detail"]


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (CandidateStatus.IN_SCREENING, {"action": "book", "start": MONDAY_10.isoformat()}),
        (CandidateStatus.QUALIFIED, {"action": "cancel"}),
    ],
)
def test_invalid_changes_are_conflicts(
    api: TestClient,
    repository: InMemoryCandidateRepository,
    status: CandidateStatus,
    body: dict[str, str],
) -> None:
    candidate = make_candidate(status=status)
    repository.save(candidate)

    response = api.post(f"/crm/candidates/{candidate.id}/interview", json=body, headers=AUTH)

    assert response.status_code == 409


def test_booking_needs_a_start(api: TestClient, repository: InMemoryCandidateRepository) -> None:
    candidate = qualified(repository)

    response = api.post(
        f"/crm/candidates/{candidate.id}/interview", json={"action": "book"}, headers=AUTH
    )

    assert response.status_code == 422


def test_without_scheduler_the_routes_are_not_mounted(
    repository: InMemoryCandidateRepository, clock: FixedClock
) -> None:
    app = FastAPI()
    app.include_router(build_crm_router(repository, clock, PASSWORD))
    candidate = qualified(repository)

    response = TestClient(app).get(f"/crm/candidates/{candidate.id}/slots", headers=AUTH)

    assert response.status_code == 404


# --- Cliente -----------------------------------------------------------------------


def test_client_books_and_cancels_through_the_api() -> None:
    candidate = make_candidate(status=CandidateStatus.INTERVIEW)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            slot = {
                "start": MONDAY_10.isoformat(),
                "end": MONDAY_10.isoformat(),
                "label": "lunes 26 de octubre, 10:00",
                "timezone": "Europe/Madrid",
            }
            return httpx.Response(200, json=[slot])
        return httpx.Response(200, json=candidate.model_dump(mode="json"))

    client = CrmClient("http://api.test", "tok", transport=httpx.MockTransport(handler))

    (slot,) = client.free_slots(candidate.id, limit=5)
    client.book_interview(candidate.id, slot.start, recruiter="Marta")
    client.cancel_interview(candidate.id)

    assert requests[0].url.path == f"/crm/candidates/{candidate.id}/slots"
    assert requests[0].url.params["limit"] == "5"
    assert json.loads(requests[1].content) == {
        "action": "book",
        "start": MONDAY_10.isoformat(),
        "recruiter": "Marta",
    }
    assert json.loads(requests[2].content) == {"action": "cancel", "recruiter": None}
