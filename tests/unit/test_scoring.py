"""Tests del puntaje (spec 004)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from screening.config import AppConfig
from screening.config.models import ScoringComponent, ScoringConfig
from screening.domain import Availability, DeliveryExperience, Schedule, ScreeningData
from screening.scoring import compute_score

TODAY = date(2026, 10, 22)


def perfect_data(**overrides: Any) -> ScreeningData:
    values: dict[str, Any] = {
        "delivery_experience": DeliveryExperience(years=3, platforms=["Glovo"]),
        "availability": Availability.FULL_TIME,
        "start_date": TODAY,
        "owns_vehicle": True,
        "preferred_schedule": Schedule.FLEXIBLE,
    }
    values.update(overrides)
    return ScreeningData(**values)


def single(component: dict[str, Any]) -> ScoringConfig:
    """Configuración con un solo componente de peso 10."""
    return ScoringConfig(
        max_score=10, components=[ScoringComponent(**{"name": "c", "weight": 10, **component})]
    )


# --- Con el scoring.yaml del repo -------------------------------------------------


def test_perfect_candidate_scores_max(app_config: AppConfig) -> None:
    result = compute_score(perfect_data(), app_config.scoring, TODAY)

    assert result.total == 100
    assert result.components == {
        "experience": 35,
        "availability": 25,
        "start_date": 20,
        "owns_vehicle": 10,
        "schedule_flexibility": 10,
    }


def test_no_experience_is_valid_and_scores_less(app_config: AppConfig) -> None:
    data = perfect_data(delivery_experience=DeliveryExperience(years=0))

    result = compute_score(data, app_config.scoring, TODAY)

    assert result.components["experience"] == 0
    assert result.total == 65


def test_empty_data_scores_zero(app_config: AppConfig) -> None:
    result = compute_score(ScreeningData(), app_config.scoring, TODAY)

    assert result.total == 0
    assert set(result.components.values()) == {0}
    assert list(result.components) == [c.name for c in app_config.scoring.components]


@pytest.mark.parametrize(
    ("years", "points"),
    [(5, 35), (2, 35), (1.99, 26.25), (1, 26.25), (0.5, 17.5), (0.49, 0), (0, 0)],
)
def test_experience_band_limits(app_config: AppConfig, years: float, points: float) -> None:
    data = ScreeningData(delivery_experience=DeliveryExperience(years=years))

    result = compute_score(data, app_config.scoring, TODAY)

    assert result.components["experience"] == points


@pytest.mark.parametrize(
    ("days", "points"),
    [(0, 20), (7, 20), (8, 12), (14, 12), (15, 6), (30, 6), (31, 0), (90, 0)],
)
def test_start_date_band_limits(app_config: AppConfig, days: int, points: float) -> None:
    data = ScreeningData(start_date=TODAY + timedelta(days=days))

    result = compute_score(data, app_config.scoring, TODAY)

    assert result.components["start_date"] == points


def test_past_start_date_counts_as_today(app_config: AppConfig) -> None:
    data = ScreeningData(start_date=TODAY - timedelta(days=10))

    result = compute_score(data, app_config.scoring, TODAY)

    assert result.components["start_date"] == 20


@pytest.mark.parametrize(
    ("availability", "points"),
    [(Availability.FULL_TIME, 25), (Availability.PART_TIME, 15), (Availability.WEEKENDS, 7.5)],
)
def test_availability_mapping(
    app_config: AppConfig, availability: Availability, points: float
) -> None:
    result = compute_score(ScreeningData(availability=availability), app_config.scoring, TODAY)

    assert result.components["availability"] == points


def test_mixed_candidate_total_rounds_half_up(app_config: AppConfig) -> None:
    data = perfect_data(
        delivery_experience=DeliveryExperience(years=1),  # 26.25
        start_date=TODAY + timedelta(days=10),  # 12
        preferred_schedule=Schedule.MORNING,  # 5
    )

    result = compute_score(data, app_config.scoring, TODAY)

    assert result.total == 78.3  # 78.25, no 78.2 del redondeo bancario


# --- Configuraciones armadas en el test -------------------------------------------


@pytest.mark.parametrize(
    ("years", "points"),
    [(0, 0), (1, 5), (2, 5), (3, 5), (3.5, 0), (4, 10), (100, 10)],
)
def test_bands_with_closed_and_open_limits(years: float, points: float) -> None:
    config = single(
        {
            "type": "bands",
            "field": "delivery_experience.years",
            "bands": [{"min": 1, "max": 3, "value": 0.5}, {"min": 4, "value": 1.0}],
        }
    )
    data = ScreeningData(delivery_experience=DeliveryExperience(years=years))

    assert compute_score(data, config, TODAY).components["c"] == points


def test_bands_first_matching_band_wins() -> None:
    config = single(
        {
            "type": "bands",
            "field": "license_years",
            "bands": [{"min": 0, "value": 0.2}, {"min": 5, "value": 1.0}],
        }
    )

    assert compute_score(ScreeningData(license_years=10), config, TODAY).total == 2


def test_bands_ignores_non_numeric_values() -> None:
    text = single({"type": "bands", "field": "city", "bands": [{"value": 1.0}]})
    flag = single({"type": "bands", "field": "owns_vehicle", "bands": [{"min": 1, "value": 1.0}]})

    assert compute_score(ScreeningData(city="Madrid"), text, TODAY).total == 0
    assert compute_score(ScreeningData(owns_vehicle=True), flag, TODAY).total == 0


def test_mapping_by_enum_value_and_missing_key() -> None:
    config = single({"type": "mapping", "field": "preferred_schedule", "mapping": {"evening": 0.4}})

    evening = ScreeningData(preferred_schedule=Schedule.EVENING)
    morning = ScreeningData(preferred_schedule=Schedule.MORNING)
    assert compute_score(evening, config, TODAY).total == 4
    assert compute_score(morning, config, TODAY).total == 0
    assert compute_score(ScreeningData(), config, TODAY).total == 0


def test_mapping_on_plain_string_field() -> None:
    config = single({"type": "mapping", "field": "city", "mapping": {"Madrid": 1.0}})

    assert compute_score(ScreeningData(city="Madrid"), config, TODAY).total == 10


@pytest.mark.parametrize(("value", "points"), [(True, 10), (False, 0), (None, 0)])
def test_boolean(value: bool | None, points: float) -> None:
    config = single({"type": "boolean", "field": "work_permit"})

    assert compute_score(ScreeningData(work_permit=value), config, TODAY).total == points


def test_boolean_only_counts_true() -> None:
    config = single({"type": "boolean", "field": "city"})

    assert compute_score(ScreeningData(city="sí"), config, TODAY).total == 0


@pytest.mark.parametrize(("days", "points"), [(-3, 10), (0, 10), (2, 10), (3, 5), (5, 5), (6, 0)])
def test_days_until(days: int, points: float) -> None:
    config = single(
        {
            "type": "days_until",
            "field": "start_date",
            "bands": [{"max": 2, "value": 1.0}, {"min": 3, "max": 5, "value": 0.5}],
        }
    )
    data = ScreeningData(start_date=TODAY + timedelta(days=days))

    assert compute_score(data, config, TODAY).total == points


def test_days_until_ignores_non_date_values() -> None:
    config = single({"type": "days_until", "field": "city", "bands": [{"value": 1.0}]})

    assert compute_score(ScreeningData(city="2026-10-22"), config, TODAY).total == 0


def test_component_points_are_rounded() -> None:
    config = single({"type": "bands", "field": "license_years", "bands": [{"value": 1 / 3}]})

    result = compute_score(ScreeningData(license_years=1), config, TODAY)

    assert result.components["c"] == 3.33
    assert result.total == 3.3
