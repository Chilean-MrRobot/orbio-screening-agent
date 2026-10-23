import pytest

from screening.config import AppConfig
from screening.domain import Country, KnockoutResult, ScreeningData, VehicleType
from screening.rules import (
    KnockoutEvaluation,
    MissingRegionError,
    PendingRule,
    SpecialConditionRegistry,
    UnknownSpecialConditionError,
    evaluate_knockouts,
)

ALL_RULES = ["consent_given", "has_license", "license_matches_vehicle", "city_in_service_area"]

VALID_ES = ScreeningData(
    consent=True,
    has_license=True,
    license_type="B",
    license_years=5,
    vehicle_type=VehicleType.MOTORCYCLE,
    city="Madrid",
)
VALID_MX = ScreeningData(
    consent=True,
    has_license=True,
    license_type="A",
    vehicle_type=VehicleType.MOTORCYCLE,
    city="Guadalajara",
)


def _with(data: ScreeningData, **changes) -> ScreeningData:
    return data.model_copy(update=changes)


def _assert_failed(evaluation: KnockoutEvaluation, rule: str, reason: str, template: str) -> None:
    assert evaluation.result == KnockoutResult(passed=False, failed_rule=rule, reason_code=reason)
    assert evaluation.message_template == template


def test_empty_data_has_no_verdict(app_config: AppConfig) -> None:
    evaluation = evaluate_knockouts(ScreeningData(), Country.ES, app_config)
    assert evaluation.result is None
    assert evaluation.passed_rules == []
    assert evaluation.message_template is None
    assert evaluation.pending_rules == [
        PendingRule(name="consent_given", missing_fields=["consent"]),
        PendingRule(name="has_license", missing_fields=["has_license"]),
        PendingRule(
            name="license_matches_vehicle", missing_fields=["license_type", "vehicle_type"]
        ),
        PendingRule(name="city_in_service_area", missing_fields=["city"]),
    ]


@pytest.mark.parametrize(("country", "data"), [(Country.ES, VALID_ES), (Country.MX, VALID_MX)])
def test_valid_candidate_passes_all_rules(
    app_config: AppConfig, country: Country, data: ScreeningData
) -> None:
    evaluation = evaluate_knockouts(data, country, app_config)
    assert evaluation.result == KnockoutResult(passed=True)
    assert evaluation.passed_rules == ALL_RULES
    assert evaluation.pending_rules == []
    assert evaluation.message_template is None


@pytest.mark.parametrize(("country", "data"), [(Country.ES, VALID_ES), (Country.MX, VALID_MX)])
def test_without_consent_fails(
    app_config: AppConfig, country: Country, data: ScreeningData
) -> None:
    evaluation = evaluate_knockouts(_with(data, consent=False), country, app_config)
    _assert_failed(evaluation, "consent_given", "no_consent", "disqualified_no_consent")
    assert evaluation.passed_rules == []


@pytest.mark.parametrize(("country", "data"), [(Country.ES, VALID_ES), (Country.MX, VALID_MX)])
def test_without_license_fails(
    app_config: AppConfig, country: Country, data: ScreeningData
) -> None:
    evaluation = evaluate_knockouts(_with(data, has_license=False), country, app_config)
    _assert_failed(evaluation, "has_license", "no_license", "disqualified_no_license")
    assert evaluation.passed_rules == ["consent_given"]


def test_es_license_b_with_two_years_fails_for_motorcycle(app_config: AppConfig) -> None:
    evaluation = evaluate_knockouts(_with(VALID_ES, license_years=2), Country.ES, app_config)
    _assert_failed(
        evaluation,
        "license_matches_vehicle",
        "license_vehicle_mismatch",
        "disqualified_license_vehicle",
    )


def test_es_license_b_with_three_years_passes_for_motorcycle(app_config: AppConfig) -> None:
    evaluation = evaluate_knockouts(_with(VALID_ES, license_years=3), Country.ES, app_config)
    assert evaluation.result == KnockoutResult(passed=True)


def test_es_license_b_without_years_is_pending(app_config: AppConfig) -> None:
    evaluation = evaluate_knockouts(_with(VALID_ES, license_years=None), Country.ES, app_config)
    assert evaluation.result is None
    assert evaluation.passed_rules == ["consent_given", "has_license", "city_in_service_area"]
    assert evaluation.pending_rules == [
        PendingRule(name="license_matches_vehicle", missing_fields=["license_years"])
    ]


def test_mx_license_b_does_not_allow_motorcycle(app_config: AppConfig) -> None:
    evaluation = evaluate_knockouts(_with(VALID_MX, license_type="b"), Country.MX, app_config)
    _assert_failed(
        evaluation,
        "license_matches_vehicle",
        "license_vehicle_mismatch",
        "disqualified_license_vehicle",
    )


@pytest.mark.parametrize("city", ["CDMX", "Ciudad de Mexico", "ciudad de méxico"])
def test_mx_city_aliases_pass(app_config: AppConfig, city: str) -> None:
    evaluation = evaluate_knockouts(_with(VALID_MX, city=city), Country.MX, app_config)
    assert evaluation.result == KnockoutResult(passed=True)


@pytest.mark.parametrize(
    ("country", "data", "city"),
    [(Country.ES, VALID_ES, "Toledo"), (Country.MX, VALID_MX, "Madrid")],
)
def test_city_out_of_service_area_fails(
    app_config: AppConfig, country: Country, data: ScreeningData, city: str
) -> None:
    evaluation = evaluate_knockouts(_with(data, city=city), country, app_config)
    _assert_failed(evaluation, "city_in_service_area", "out_of_service_area", "disqualified_city")


def test_incremental_evaluation(app_config: AppConfig) -> None:
    data = ScreeningData(consent=True)
    evaluation = evaluate_knockouts(data, Country.ES, app_config)
    assert evaluation.result is None
    assert evaluation.passed_rules == ["consent_given"]
    assert [p.name for p in evaluation.pending_rules] == ALL_RULES[1:]

    data = _with(data, has_license=True, license_type="B")
    evaluation = evaluate_knockouts(data, Country.ES, app_config)
    assert evaluation.passed_rules == ["consent_given", "has_license"]
    assert evaluation.pending_rules[0] == PendingRule(
        name="license_matches_vehicle", missing_fields=["vehicle_type"]
    )

    data = _with(data, vehicle_type=VehicleType.CAR, city="Valencia")
    evaluation = evaluate_knockouts(data, Country.ES, app_config)
    assert evaluation.result == KnockoutResult(passed=True)


def test_later_rule_fails_while_earlier_is_pending(app_config: AppConfig) -> None:
    data = ScreeningData(consent=True, city="Toledo")
    evaluation = evaluate_knockouts(data, Country.ES, app_config)
    _assert_failed(evaluation, "city_in_service_area", "out_of_service_area", "disqualified_city")
    assert evaluation.passed_rules == ["consent_given"]
    assert [p.name for p in evaluation.pending_rules] == ["has_license", "license_matches_vehicle"]


def test_stops_at_first_failure(app_config: AppConfig) -> None:
    data = _with(VALID_ES, has_license=False, city="Toledo")
    evaluation = evaluate_knockouts(data, Country.ES, app_config)
    _assert_failed(evaluation, "has_license", "no_license", "disqualified_no_license")
    assert evaluation.passed_rules == ["consent_given"]
    assert evaluation.pending_rules == []


def test_disabled_rules_are_skipped(app_config: AppConfig) -> None:
    rules = [
        r.model_copy(update={"enabled": False}) if r.name == "city_in_service_area" else r
        for r in app_config.knockout_rules
    ]
    config = app_config.model_copy(update={"knockout_rules": rules})
    evaluation = evaluate_knockouts(_with(VALID_ES, city="Toledo"), Country.ES, config)
    assert evaluation.result == KnockoutResult(passed=True)
    assert "city_in_service_area" not in evaluation.passed_rules


def test_country_without_region_raises(app_config: AppConfig) -> None:
    config = app_config.model_copy(update={"regions": {Country.ES: app_config.regions[Country.ES]}})
    with pytest.raises(MissingRegionError, match="MX"):
        evaluate_knockouts(VALID_MX, Country.MX, config)


def test_unregistered_special_condition_raises_even_if_pending(app_config: AppConfig) -> None:
    with pytest.raises(UnknownSpecialConditionError, match="license_compatible"):
        evaluate_knockouts(
            ScreeningData(), Country.ES, app_config, registry=SpecialConditionRegistry()
        )
