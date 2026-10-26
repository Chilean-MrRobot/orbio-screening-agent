"""Generación, reparto y formato de horarios de entrevista (spec 021). Funciones puras."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from screening.config import AppConfig
from screening.config.models import CountryScheduling
from screening.domain import Country, InterviewSlot, Language
from screening.scheduling import format_options, format_slot, generate_slots, pick_options, slot_id

MADRID = "Europe/Madrid"
MEXICO = "America/Mexico_City"
# Jueves 22/10/2026 10:00 UTC (12:00 en Madrid, horario de verano hasta el domingo 25).
NOW = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)


def _spec(app_config: AppConfig, country: Country) -> CountryScheduling:
    assert app_config.scheduling is not None
    return app_config.scheduling.countries[country]


def _slots(spec: CountryScheduling, timezone: str, days: int = 7) -> list[InterviewSlot]:
    start = NOW + timedelta(hours=24)
    return generate_slots(
        spec, timezone, slot_minutes=30, start=start, end=start + timedelta(days=days)
    )


def _local(slot: InterviewSlot, timezone: str) -> datetime:
    return slot.start.astimezone(ZoneInfo(timezone))


def test_es_slots_follow_windows_weekdays_and_notice(app_config: AppConfig) -> None:
    slots = _slots(_spec(app_config, Country.ES), MADRID)

    local = [_local(s, MADRID) for s in slots]
    # El primero respeta las 24 h de antelación: viernes 23 a las 12:00 (10:00 UTC).
    assert slots[0].start == NOW + timedelta(hours=24)
    assert local[0].strftime("%a %d %H:%M") == "Fri 23 12:00"
    assert all(s.start >= NOW + timedelta(hours=24) for s in slots)
    # Solo de lunes a viernes, dentro de 10–13 y 16–18, cada 30 min.
    assert {d.isoweekday() for d in local} <= {1, 2, 3, 4, 5}
    friday = [d.strftime("%H:%M") for d in local if d.day == 23]
    assert friday == ["12:00", "12:30", "16:00", "16:30", "17:00", "17:30"]
    monday = [d.strftime("%H:%M") for d in local if d.day == 26]
    assert monday[0] == "10:00" and monday[-1] == "17:30" and len(monday) == 10
    assert all(s.end - s.start == timedelta(minutes=30) for s in slots)


def test_es_slots_respect_the_daylight_saving_change(app_config: AppConfig) -> None:
    slots = _slots(_spec(app_config, Country.ES), MADRID)

    friday_noon = next(s for s in slots if _local(s, MADRID).day == 23)
    monday_ten = next(s for s in slots if _local(s, MADRID).day == 26)
    assert friday_noon.start.hour == 10  # 12:00 CEST = 10:00 UTC
    assert monday_ten.start == datetime(2026, 10, 26, 9, 0, tzinfo=UTC)  # 10:00 CET


def test_mx_slots_use_mexico_city_time(app_config: AppConfig) -> None:
    slots = _slots(_spec(app_config, Country.MX), MEXICO)

    first = _local(slots[0], MEXICO)
    assert first.strftime("%a %d %H:%M") == "Fri 23 10:00"
    assert slots[0].start == datetime(2026, 10, 23, 16, 0, tzinfo=UTC)
    friday = [s for s in slots if _local(s, MEXICO).day == 23]
    assert len(friday) == 8  # 10:00 a 13:30


def test_slots_stop_at_the_horizon(app_config: AppConfig) -> None:
    slots = _slots(_spec(app_config, Country.ES), MADRID, days=1)

    assert {_local(s, MADRID).day for s in slots} == {23}


def test_slot_id_orders_like_the_start_and_is_in_utc() -> None:
    start = datetime(2026, 10, 26, 10, 0, tzinfo=ZoneInfo(MADRID))

    assert slot_id("ES", start) == "ES_20261026T0900Z"
    assert slot_id("ES", start) < slot_id("ES", start + timedelta(minutes=30))


def test_pick_options_spreads_over_different_days(app_config: AppConfig) -> None:
    slots = _slots(_spec(app_config, Country.ES), MADRID)

    options = pick_options(slots, 3, MADRID)

    days = [_local(s, MADRID).day for s in options]
    assert days == [23, 26, 27]
    assert options[0] == slots[0]


def test_pick_options_fills_with_the_same_day_when_there_are_few_days(
    app_config: AppConfig,
) -> None:
    slots = _slots(_spec(app_config, Country.ES), MADRID, days=1)

    options = pick_options(slots, 3, MADRID)

    assert options == slots[:3]


def test_format_slot_in_spanish_and_english() -> None:
    slot = InterviewSlot(
        start=datetime(2026, 10, 27, 8, 0, tzinfo=UTC),
        end=datetime(2026, 10, 27, 8, 30, tzinfo=UTC),
    )

    assert format_slot(slot, MADRID, Language.ES) == "martes 27 de octubre, 09:00"
    assert format_slot(slot, MADRID, Language.EN) == "Tuesday, October 27, 09:00"
    assert format_slot(slot, MEXICO, Language.ES) == "martes 27 de octubre, 02:00"
    assert format_options([slot, slot], MADRID, Language.ES)[1].startswith("2) martes")


@pytest.mark.parametrize("weekday", [6, 7])
def test_weekend_days_can_be_configured(app_config: AppConfig, weekday: int) -> None:
    spec = _spec(app_config, Country.ES).model_copy(update={"days": [weekday]})

    slots = _slots(spec, MADRID)

    assert slots and {_local(s, MADRID).isoweekday() for s in slots} == {weekday}
