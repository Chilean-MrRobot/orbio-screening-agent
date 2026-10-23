from datetime import date
from typing import Any

import pytest

from screening.config import AppConfig
from screening.config.models import Condition
from screening.domain import (
    Availability,
    Country,
    DeliveryExperience,
    ScreeningData,
    VehicleType,
)
from screening.rules import (
    EvaluationContext,
    Outcome,
    RuleEvaluationError,
    default_registry,
    evaluate_condition,
)


@pytest.fixture
def context(app_config: AppConfig) -> EvaluationContext:
    return EvaluationContext(region=app_config.regions[Country.ES], registry=default_registry())


def _eval(raw: dict[str, Any], data: ScreeningData, context: EvaluationContext) -> Outcome:
    return evaluate_condition(Condition.model_validate(raw), data, context)


FULL = ScreeningData(
    consent=True,
    license_years=4,
    vehicle_type=VehicleType.MOTORCYCLE,
    city="Madrid",
    availability=Availability.FULL_TIME,
    delivery_experience=DeliveryExperience(years=2),
    start_date=date(2026, 11, 1),
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"field": "consent", "operator": "equals", "value": True}, True),
        ({"field": "consent", "operator": "equals", "value": False}, False),
        ({"field": "consent", "operator": "not_equals", "value": False}, True),
        ({"field": "vehicle_type", "operator": "equals", "value": "motorcycle"}, True),
        ({"field": "vehicle_type", "operator": "not_equals", "value": "motorcycle"}, False),
        ({"field": "license_years", "operator": "greater_than", "value": 3}, True),
        ({"field": "license_years", "operator": "greater_than", "value": 4}, False),
        ({"field": "license_years", "operator": "less_than", "value": 5}, True),
        ({"field": "license_years", "operator": "less_than", "value": 4}, False),
        ({"field": "license_years", "operator": "greater_or_equal", "value": 4}, True),
        ({"field": "license_years", "operator": "greater_or_equal", "value": 5}, False),
        ({"field": "license_years", "operator": "less_or_equal", "value": 4}, True),
        ({"field": "license_years", "operator": "less_or_equal", "value": 3}, False),
        ({"field": "availability", "operator": "in", "values": ["full_time", "weekends"]}, True),
        ({"field": "availability", "operator": "in", "values": ["weekends"]}, False),
        ({"field": "availability", "operator": "not_in", "values": ["weekends"]}, True),
        ({"field": "availability", "operator": "not_in", "values": ["full_time"]}, False),
        ({"field": "license_years", "operator": "between", "min": 4, "max": 10}, True),
        ({"field": "license_years", "operator": "between", "min": 1, "max": 4}, True),
        ({"field": "license_years", "operator": "between", "min": 5, "max": 10}, False),
        ({"field": "city", "operator": "exists"}, True),
        ({"field": "city", "operator": "not_exists"}, False),
        ({"field": "delivery_experience.years", "operator": "greater_or_equal", "value": 1}, True),
        ({"field": "start_date", "operator": "less_than", "value": date(2026, 12, 1)}, True),
    ],
)
def test_operators_on_known_values(
    raw: dict[str, Any], expected: bool, context: EvaluationContext
) -> None:
    outcome = _eval(raw, FULL, context)
    assert outcome.value is expected
    assert outcome.missing == frozenset()


@pytest.mark.parametrize(
    "raw",
    [
        {"field": "consent", "operator": "equals", "value": True},
        {"field": "consent", "operator": "not_equals", "value": True},
        {"field": "license_years", "operator": "greater_than", "value": 1},
        {"field": "license_years", "operator": "less_than", "value": 1},
        {"field": "license_years", "operator": "greater_or_equal", "value": 1},
        {"field": "license_years", "operator": "less_or_equal", "value": 1},
        {"field": "license_years", "operator": "in", "values": [1, 2]},
        {"field": "license_years", "operator": "not_in", "values": [1, 2]},
        {"field": "license_years", "operator": "between", "min": 1, "max": 2},
    ],
)
def test_missing_field_is_unknown(raw: dict[str, Any], context: EvaluationContext) -> None:
    outcome = _eval(raw, ScreeningData(), context)
    assert outcome.value is None
    assert outcome.missing == frozenset({raw["field"]})


def test_exists_operators_always_decide(context: EvaluationContext) -> None:
    empty = ScreeningData()
    assert _eval({"field": "city", "operator": "exists"}, empty, context).value is False
    assert _eval({"field": "city", "operator": "not_exists"}, empty, context).value is True


def test_blank_text_counts_as_missing(context: EvaluationContext) -> None:
    data = ScreeningData(city="   ")
    assert _eval({"field": "city", "operator": "exists"}, data, context).value is False
    outcome = _eval({"field": "city", "operator": "equals", "value": "Madrid"}, data, context)
    assert outcome.value is None


def test_dotted_path_with_missing_parent_is_unknown(context: EvaluationContext) -> None:
    raw = {"field": "delivery_experience.years", "operator": "greater_than", "value": 1}
    outcome = _eval(raw, ScreeningData(), context)
    assert outcome.value is None
    assert outcome.missing == frozenset({"delivery_experience.years"})


def test_incompatible_types_raise_clear_error(context: EvaluationContext) -> None:
    raw = {"field": "city", "operator": "greater_than", "value": 3}
    with pytest.raises(RuleEvaluationError, match="city"):
        _eval(raw, FULL, context)


# --- Combinaciones con desconocidos -----------------------------------------------

TRUE = {"field": "consent", "operator": "equals", "value": True}
FALSE = {"field": "consent", "operator": "equals", "value": False}
UNKNOWN_A = {"field": "has_license", "operator": "equals", "value": True}
UNKNOWN_B = {"field": "owns_vehicle", "operator": "equals", "value": True}
DATA = ScreeningData(consent=True)


@pytest.mark.parametrize(
    ("children", "expected", "missing"),
    [
        ([TRUE, TRUE], True, set()),
        ([TRUE, FALSE], False, set()),
        ([UNKNOWN_A, FALSE], False, set()),
        ([TRUE, UNKNOWN_A], None, {"has_license"}),
        ([UNKNOWN_A, UNKNOWN_B], None, {"has_license", "owns_vehicle"}),
    ],
)
def test_all(children, expected, missing, context: EvaluationContext) -> None:
    outcome = _eval({"all": children}, DATA, context)
    assert outcome.value is expected
    assert outcome.missing == frozenset(missing)


@pytest.mark.parametrize(
    ("children", "expected", "missing"),
    [
        ([FALSE, TRUE], True, set()),
        ([FALSE, FALSE], False, set()),
        ([UNKNOWN_A, TRUE], True, set()),
        ([FALSE, UNKNOWN_A], None, {"has_license"}),
        ([UNKNOWN_A, UNKNOWN_B], None, {"has_license", "owns_vehicle"}),
    ],
)
def test_any(children, expected, missing, context: EvaluationContext) -> None:
    outcome = _eval({"any": children}, DATA, context)
    assert outcome.value is expected
    assert outcome.missing == frozenset(missing)


@pytest.mark.parametrize(
    ("child", "expected"),
    [(TRUE, False), (FALSE, True), (UNKNOWN_A, None)],
)
def test_not(child, expected, context: EvaluationContext) -> None:
    outcome = _eval({"not": child}, DATA, context)
    assert outcome.value is expected


def test_nested_combinations(context: EvaluationContext) -> None:
    raw = {"all": [TRUE, {"not": {"any": [FALSE, UNKNOWN_A]}}]}
    outcome = _eval(raw, DATA, context)
    assert outcome.value is None
    assert outcome.missing == frozenset({"has_license"})

    decided = _eval(raw, DATA.model_copy(update={"has_license": True}), context)
    assert decided.value is False


def test_special_condition_is_dispatched(context: EvaluationContext) -> None:
    outcome = _eval({"special": "city_in_service_area"}, ScreeningData(city="madrid"), context)
    assert outcome.value is True
