from __future__ import annotations

from screening.config import AppConfig
from screening.conversation import current_stage, missing_fields
from screening.domain import Candidate, ScreeningData, Stage, VehicleType
from screening.rules import evaluate_knockouts


def _with(candidate: Candidate, **data: object) -> Candidate:
    candidate.data = ScreeningData(**data)
    return candidate


def _missing(candidate: Candidate, stage: Stage, config: AppConfig) -> list[str]:
    evaluation = evaluate_knockouts(candidate.data, candidate.country, config)  # type: ignore[arg-type]
    return missing_fields(candidate, stage, evaluation, config)


def test_welcome_asks_name_then_consent(candidate: Candidate, app_config: AppConfig) -> None:
    assert _missing(_with(candidate), Stage.WELCOME, app_config) == ["full_name", "consent"]
    assert _missing(_with(candidate, full_name="Lucía"), Stage.WELCOME, app_config) == ["consent"]


def test_knockout_fields_follow_rule_order(candidate: Candidate, app_config: AppConfig) -> None:
    assert _missing(_with(candidate, consent=True), Stage.KNOCKOUT, app_config) == [
        "has_license",
        "license_type",
        "vehicle_type",
        "city",
    ]


def test_license_years_appears_when_the_rule_needs_it(
    candidate: Candidate, app_config: AppConfig
) -> None:
    _with(
        candidate,
        consent=True,
        has_license=True,
        license_type="B",
        vehicle_type=VehicleType.MOTORCYCLE,
        city="Madrid",
    )

    assert _missing(candidate, Stage.KNOCKOUT, app_config) == ["license_years"]


def test_profile_fields_come_from_scoring_then_design(
    candidate: Candidate, app_config: AppConfig
) -> None:
    assert _missing(_with(candidate), Stage.PROFILE, app_config) == [
        "delivery_experience",
        "availability",
        "start_date",
        "owns_vehicle",
        "preferred_schedule",
        "work_permit",
    ]


def test_needs_review_fields_are_not_asked_again(
    candidate: Candidate, app_config: AppConfig
) -> None:
    _with(candidate, consent=True, needs_review=["has_license"])

    assert "has_license" not in _missing(candidate, Stage.KNOCKOUT, app_config)


def test_current_stage_is_first_stage_with_missing_fields(
    candidate: Candidate, app_config: AppConfig
) -> None:
    def stage() -> Stage:
        evaluation = evaluate_knockouts(candidate.data, candidate.country, app_config)  # type: ignore[arg-type]
        return current_stage(candidate, evaluation, app_config)

    _with(candidate, full_name="Lucía")
    assert stage() == Stage.WELCOME

    _with(candidate, full_name="Lucía", consent=True)
    assert stage() == Stage.KNOCKOUT

    knockout = dict(
        full_name="Lucía",
        consent=True,
        has_license=True,
        license_type="B",
        vehicle_type=VehicleType.CAR,
        city="Madrid",
    )
    _with(candidate, **knockout)
    assert stage() == Stage.PROFILE

    _with(
        candidate,
        **knockout,
        needs_review=[
            "delivery_experience",
            "availability",
            "start_date",
            "owns_vehicle",
            "preferred_schedule",
            "work_permit",
        ],
    )
    assert stage() == Stage.CLOSING
