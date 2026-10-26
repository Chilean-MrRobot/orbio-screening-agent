"""`schedule_next_touch`: el próximo toque en cada situación y en horario hábil (spec 017)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from screening.config import AppConfig
from screening.domain import Candidate, CandidateStatus, Country
from screening.reengagement import refresh_next_touch, schedule_next_touch
from tests.unit.reengagement.conftest import (
    ES_PHONE,
    MONDAY,
    MX_PHONE,
    Harness,
    abandoned,
    contacted,
    with_cadences,
)

H = timedelta(hours=1)

# --- Qué cadencia y qué paso --------------------------------------------------------


def test_contacted_waits_for_the_first_reminder(app_config: AppConfig) -> None:
    # El `first_contact` ya salió: el próximo es el recordatorio de las 24 h.
    assert schedule_next_touch(contacted(MONDAY), app_config) == MONDAY + 24 * H


def test_new_candidate_whose_first_contact_failed_is_due_now(app_config: AppConfig) -> None:
    candidate = Candidate(
        application=contacted(MONDAY).application,
        phone=ES_PHONE,
        country=Country.ES,
        created_at=MONDAY,
    )

    assert schedule_next_touch(candidate, app_config) == MONDAY


def test_abandoned_counts_from_the_last_agent_message(app_config: AppConfig) -> None:
    assert schedule_next_touch(abandoned(MONDAY), app_config) == MONDAY + 24 * H


def test_candidate_waiting_for_the_agent_has_no_touch(app_config: AppConfig) -> None:
    candidate = abandoned(MONDAY)
    candidate.messages.pop()  # el último mensaje es del candidato

    assert schedule_next_touch(candidate, app_config) is None


@pytest.mark.parametrize(
    "status",
    [
        CandidateStatus.QUALIFIED,
        CandidateStatus.DISQUALIFIED,
        CandidateStatus.OPTED_OUT,
        CandidateStatus.INVALID_DATA,
        CandidateStatus.NO_RESPONSE,
        CandidateStatus.INTERVIEW,
        CandidateStatus.HIRED,
    ],
)
def test_final_statuses_have_no_touch(app_config: AppConfig, status: CandidateStatus) -> None:
    candidate = contacted(MONDAY)
    candidate.status = status

    assert schedule_next_touch(candidate, app_config) is None


def test_human_requested_has_no_touch(app_config: AppConfig) -> None:
    candidate = abandoned(MONDAY)
    candidate.human_requested = True

    assert schedule_next_touch(candidate, app_config) is None


def test_candidate_without_phone_has_no_touch(app_config: AppConfig) -> None:
    assert schedule_next_touch(contacted(MONDAY, phone=None), app_config) is None


def test_country_without_business_hours_has_no_touch(app_config: AppConfig) -> None:
    hours = {Country.ES: app_config.cadences.business_hours[Country.ES]}
    config = with_cadences(app_config, business_hours=hours)

    assert (
        schedule_next_touch(abandoned(MONDAY, phone=MX_PHONE, country=Country.MX), config) is None
    )


def test_active_cadence_continues_from_its_next_step(harness: Harness) -> None:
    cid = harness.add(contacted(MONDAY))
    harness.run_at(MONDAY + 24 * H)

    candidate = harness.get(cid)
    assert candidate.reengagement.next_step_index == 2
    assert schedule_next_touch(candidate, harness.config) == MONDAY + 72 * H
    assert candidate.reengagement.next_touch_at == MONDAY + 72 * H


def test_finished_cadence_has_no_touch(harness: Harness) -> None:
    cid = harness.add(contacted(MONDAY))
    for hours in (24, 72, 120):
        harness.run_at(MONDAY + hours * H)

    candidate = harness.get(cid)
    assert candidate.status == CandidateStatus.NO_RESPONSE
    assert schedule_next_touch(candidate, harness.config) is None


def test_reply_after_the_anchor_restarts_from_the_new_silence(harness: Harness) -> None:
    cid = harness.add(abandoned(MONDAY))
    harness.run_at(MONDAY + 24 * H)

    candidate = harness.get(cid)
    later = MONDAY + 30 * H
    candidate.last_candidate_message_at = later - timedelta(minutes=1)
    candidate.last_agent_message_at = later

    assert schedule_next_touch(candidate, harness.config) == later + 24 * H


def test_refresh_next_touch_writes_the_field(app_config: AppConfig) -> None:
    candidate = contacted(MONDAY)
    assert candidate.reengagement.next_touch_at is None

    refresh_next_touch(candidate, app_config)

    assert candidate.reengagement.next_touch_at == MONDAY + 24 * H


# --- Horario hábil ------------------------------------------------------------------


def test_spain_sunday_moves_to_monday_opening_after_the_time_change(
    app_config: AppConfig,
) -> None:
    # Sábado 24/10 12:00 Madrid; +24 h cae en domingo 25/10, día en que termina el horario
    # de verano: la apertura del lunes es a las 09:00 CET = 08:00 UTC.
    anchor = datetime(2026, 10, 24, 10, 0, tzinfo=UTC)

    due = schedule_next_touch(abandoned(anchor), app_config)

    assert due == datetime(2026, 10, 26, 8, 0, tzinfo=UTC)


def test_spain_after_closing_moves_to_next_morning(app_config: AppConfig) -> None:
    anchor = datetime(2026, 10, 19, 19, 30, tzinfo=UTC)  # lunes 21:30 en Madrid

    due = schedule_next_touch(abandoned(anchor), app_config)

    assert due == datetime(2026, 10, 21, 9, 0, tzinfo=ZoneInfo("Europe/Madrid"))


def test_mexico_dawn_moves_to_the_local_opening(app_config: AppConfig) -> None:
    anchor = MONDAY - 2 * H  # 02:00 en Ciudad de México

    due = schedule_next_touch(abandoned(anchor, phone=MX_PHONE, country=Country.MX), app_config)

    assert due == datetime(2026, 10, 20, 9, 0, tzinfo=ZoneInfo("America/Mexico_City"))
    assert due == datetime(2026, 10, 20, 15, 0, tzinfo=UTC)


def test_same_anchor_is_inside_hours_in_spain(app_config: AppConfig) -> None:
    anchor = MONDAY - 2 * H  # 10:00 en Madrid

    assert schedule_next_touch(abandoned(anchor), app_config) == anchor + 24 * H


def test_without_queueing_the_nominal_hour_is_kept(app_config: AppConfig) -> None:
    config = with_cadences(app_config, queue_outside_hours=False)
    anchor = MONDAY - 2 * H

    due = schedule_next_touch(abandoned(anchor, phone=MX_PHONE, country=Country.MX), config)

    assert due == anchor + 24 * H
