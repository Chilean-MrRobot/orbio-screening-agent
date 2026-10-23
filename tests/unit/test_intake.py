from __future__ import annotations

from typing import Any

import pytest

from screening.adapters.memory import InMemoryCandidateRepository
from screening.clock import FixedClock
from screening.config import AppConfig
from screening.domain import CandidateStatus, Country, Stage
from screening.intake import IntakeOutcome, IntakeReason, IntakeResult, process_application


def payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "application_id": "app-100",
        "source": "infojobs",
        "full_name": "Lucía Pérez",
        "phone": "+34 600 111 222",
        "email": "lucia@example.com",
        "country": "ES",
        "city": "Madrid",
        "vacancy_id": "rep-mad-01",
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


@pytest.fixture
def process(repository: InMemoryCandidateRepository, clock: FixedClock, app_config: AppConfig):
    def _process(raw: dict[str, Any]) -> IntakeResult:
        return process_application(raw, repository=repository, clock=clock, config=app_config)

    return _process


def event_types(result: IntakeResult) -> list[str]:
    assert result.candidate is not None
    return [e.type for e in result.candidate.timeline]


# --- Caso feliz -------------------------------------------------------------------


def test_creates_spanish_candidate(process, repository, clock):
    result = process(payload())

    assert result.outcome == IntakeOutcome.CREATED
    assert result.reason is None
    candidate = result.candidate
    assert candidate is not None
    assert candidate.phone == "+34600111222"
    assert candidate.country == Country.ES
    assert candidate.status == CandidateStatus.NEW
    assert candidate.stage == Stage.WELCOME
    assert candidate.data.full_name == "Lucía Pérez"
    assert candidate.created_at == clock.now()
    assert candidate.updated_at == clock.now()
    assert event_types(result) == ["application_received"]
    assert candidate.timeline[0].at == clock.now()
    assert repository.get(candidate.id) == candidate


def test_creates_mexican_candidate_from_local_number(process):
    result = process(payload(phone="55 1234 5678", country="MX", city="CDMX"))

    assert result.outcome == IntakeOutcome.CREATED
    assert result.candidate is not None
    assert result.candidate.phone == "+525512345678"
    assert result.candidate.country == Country.MX


@pytest.mark.parametrize(
    "raw_phone",
    ["600111222", "600-111-222", "(+34) 600 111 222", "0034 600 111 222", " +34600111222 "],
)
def test_normalizes_phone_formats(process, raw_phone):
    result = process(payload(phone=raw_phone))

    assert result.outcome == IntakeOutcome.CREATED
    assert result.candidate is not None
    assert result.candidate.phone == "+34600111222"


def test_does_not_prefill_knockout_fields(process):
    result = process(payload())

    assert result.candidate is not None
    data = result.candidate.data
    assert data.city is None
    assert data.has_license is None
    assert data.vehicle_type is None
    assert result.candidate.application.city == "Madrid"


# --- País inferido ------------------------------------------------------------------


def test_infers_country_from_international_prefix(process):
    result = process(payload(phone="+52 55 1234 5678", country=None))

    assert result.outcome == IntakeOutcome.CREATED
    assert result.candidate is not None
    assert result.candidate.country == Country.MX
    assert result.candidate.phone == "+525512345678"


def test_infers_country_from_local_number_valid_in_one_region(process):
    result = process(payload(phone="600 111 222", country=None))

    assert result.outcome == IntakeOutcome.CREATED
    assert result.candidate is not None
    assert result.candidate.country == Country.ES


def test_country_mismatch_keeps_payload_country_and_records_event(process):
    result = process(payload(phone="+52 55 1234 5678", country="ES"))

    assert result.outcome == IntakeOutcome.CREATED
    assert result.candidate is not None
    assert result.candidate.country == Country.ES
    assert result.candidate.phone == "+525512345678"
    mismatch = next(e for e in result.candidate.timeline if e.type == "country_mismatch")
    assert mismatch.detail == {"payload_country": Country.ES, "phone_country": Country.MX}


# --- Datos inválidos ----------------------------------------------------------------


def test_missing_phone_creates_invalid_data_candidate(process, repository):
    result = process(payload(phone=None))

    assert result.outcome == IntakeOutcome.INVALID_DATA
    assert result.reason == IntakeReason.PHONE_MISSING
    candidate = result.candidate
    assert candidate is not None
    assert candidate.phone is None
    assert candidate.status == CandidateStatus.INVALID_DATA
    assert event_types(result) == ["application_received", "status_changed"]
    assert candidate.timeline[0].detail["email"] == "lucia@example.com"
    assert candidate.timeline[1].detail["reason"] == IntakeReason.PHONE_MISSING
    assert repository.list(CandidateStatus.INVALID_DATA) == [candidate]


def test_blank_phone_counts_as_missing(process):
    result = process(payload(phone="   "))

    assert result.reason == IntakeReason.PHONE_MISSING


@pytest.mark.parametrize("raw_phone", ["123", "no tengo", "+34 123", "600 111"])
def test_invalid_phone_creates_invalid_data_candidate(process, raw_phone):
    result = process(payload(phone=raw_phone))

    assert result.outcome == IntakeOutcome.INVALID_DATA
    assert result.reason == IntakeReason.PHONE_INVALID
    assert result.candidate is not None
    assert result.candidate.status == CandidateStatus.INVALID_DATA
    assert result.candidate.country == Country.ES
    assert result.errors


def test_phone_from_country_not_served(process):
    result = process(payload(phone="+54 9 11 2345 6789", country=None))

    assert result.outcome == IntakeOutcome.INVALID_DATA
    assert result.reason == IntakeReason.COUNTRY_NOT_SERVED
    assert result.candidate is not None
    assert result.candidate.country is None


# --- Duplicados ---------------------------------------------------------------------


def test_duplicate_phone_joins_existing_candidate(process, repository, clock):
    first = process(payload())
    clock.advance(hours=2)

    second = process(payload(application_id="app-200", source="indeed", phone="600111222"))

    assert second.outcome == IntakeOutcome.DUPLICATE
    assert second.candidate is not None
    assert first.candidate is not None
    assert second.candidate.id == first.candidate.id
    assert len(repository.list()) == 1
    event = second.candidate.timeline[-1]
    assert event.type == "duplicate_application"
    assert event.at == clock.now()
    assert event.detail["application_id"] == "app-200"
    assert event.detail["source"] == "indeed"
    stored = repository.get(first.candidate.id)
    assert stored is not None
    assert stored.timeline[-1].type == "duplicate_application"
    assert stored.application.application_id == "app-100"
    assert stored.status == CandidateStatus.NEW


# --- Payload rechazado --------------------------------------------------------------


@pytest.mark.parametrize("missing", ["application_id", "source"])
def test_rejects_payload_without_required_field(process, repository, missing):
    raw = payload()
    del raw[missing]

    result = process(raw)

    assert result.outcome == IntakeOutcome.REJECTED
    assert result.reason == IntakeReason.INVALID_PAYLOAD
    assert result.candidate is None
    assert any(missing in error for error in result.errors)
    assert repository.list() == []


@pytest.mark.parametrize("field", ["application_id", "source"])
def test_rejects_blank_required_field(process, repository, field):
    result = process(payload(**{field: "  "}))

    assert result.outcome == IntakeOutcome.REJECTED
    assert repository.list() == []


def test_ignores_extra_fields(process):
    result = process(payload(utm_campaign="verano", salary_expectation=1200))

    assert result.outcome == IntakeOutcome.CREATED
    assert event_types(result) == ["application_received"]


def test_drops_invalid_optional_field_and_infers_country(process):
    result = process(payload(country="AR", applied_at="ayer"))

    assert result.outcome == IntakeOutcome.CREATED
    candidate = result.candidate
    assert candidate is not None
    assert candidate.country == Country.ES
    assert candidate.application.country is None
    ignored = next(e for e in candidate.timeline if e.type == "fields_ignored")
    assert sorted(ignored.detail["fields"]) == ["applied_at", "country"]
