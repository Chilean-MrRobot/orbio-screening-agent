import pytest

from screening.config import AppConfig
from screening.domain import Country, ScreeningData, VehicleType
from screening.rules import (
    Outcome,
    SpecialConditionRegistry,
    UnknownSpecialConditionError,
    city_in_service_area,
    default_registry,
    license_compatible,
    normalize_text,
)

MOTO = VehicleType.MOTORCYCLE
CAR = VehicleType.CAR


@pytest.mark.parametrize(
    ("country", "license_type", "vehicle", "years", "expected", "missing"),
    [
        # España
        (Country.ES, "B", CAR, None, True, set()),
        (Country.ES, "b", MOTO, 3, True, set()),
        (Country.ES, "B", MOTO, 5, True, set()),
        (Country.ES, "B", MOTO, 2, False, set()),
        (Country.ES, "B", MOTO, None, None, {"license_years"}),
        (Country.ES, " a2 ", MOTO, None, True, set()),
        (Country.ES, "AM", CAR, None, False, set()),
        (Country.ES, "C", CAR, None, False, set()),
        # México
        (Country.MX, "A", MOTO, None, True, set()),
        (Country.MX, "B", CAR, None, True, set()),
        (Country.MX, "B", MOTO, None, False, set()),
        (Country.MX, "Motociclista", MOTO, None, True, set()),
        (Country.MX, "motociclista", CAR, None, False, set()),
    ],
)
def test_license_compatible(
    app_config: AppConfig,
    country: Country,
    license_type: str,
    vehicle: VehicleType,
    years: float | None,
    expected: bool | None,
    missing: set[str],
) -> None:
    data = ScreeningData(license_type=license_type, vehicle_type=vehicle, license_years=years)
    outcome = license_compatible(data, app_config.regions[country])
    assert outcome.value is expected
    assert outcome.missing == frozenset(missing)


@pytest.mark.parametrize(
    ("data", "missing"),
    [
        (ScreeningData(vehicle_type=MOTO), {"license_type"}),
        (ScreeningData(license_type="B"), {"vehicle_type"}),
        (ScreeningData(), {"license_type", "vehicle_type"}),
    ],
)
def test_license_compatible_unknown_without_data(
    app_config: AppConfig, data: ScreeningData, missing: set[str]
) -> None:
    outcome = license_compatible(data, app_config.regions[Country.ES])
    assert outcome.value is None
    assert outcome.missing == frozenset(missing)


@pytest.mark.parametrize(
    ("country", "city", "expected"),
    [
        (Country.ES, "Madrid", True),
        (Country.ES, "  madrid ", True),
        (Country.ES, "Malaga", True),
        (Country.ES, "MÁLAGA", True),
        (Country.ES, "Toledo", False),
        (Country.MX, "CDMX", True),
        (Country.MX, "cdmx", True),
        (Country.MX, "Ciudad de Mexico", True),
        (Country.MX, "ciudad   de  méxico", True),
        (Country.MX, "México DF", True),
        (Country.MX, "Queretaro", True),
        (Country.MX, "Cancún", False),
        (Country.MX, "Madrid", False),
    ],
)
def test_city_in_service_area(
    app_config: AppConfig, country: Country, city: str, expected: bool
) -> None:
    outcome = city_in_service_area(ScreeningData(city=city), app_config.regions[country])
    assert outcome.value is expected


def test_city_unknown_without_city(app_config: AppConfig) -> None:
    outcome = city_in_service_area(ScreeningData(), app_config.regions[Country.ES])
    assert outcome.value is None
    assert outcome.missing == frozenset({"city"})


def test_normalize_text() -> None:
    assert normalize_text("  Ciudad   de  MÉXICO ") == "ciudad de mexico"
    assert normalize_text("Querétaro") == "queretaro"


def test_default_registry_has_engine_specials() -> None:
    registry = default_registry()
    assert registry.get("license_compatible") is license_compatible
    assert registry.get("city_in_service_area") is city_in_service_area


def test_registry_unknown_name_raises_clear_error() -> None:
    with pytest.raises(UnknownSpecialConditionError, match="no_such_condition"):
        SpecialConditionRegistry().get("no_such_condition")


def test_registry_accepts_custom_functions() -> None:
    registry = SpecialConditionRegistry()
    registry.register("always", lambda data, region: Outcome(True))
    assert "always" in registry
    assert registry.get("always")(ScreeningData(), None).value is True  # type: ignore[arg-type]
