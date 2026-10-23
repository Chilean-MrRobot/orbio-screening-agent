from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from screening.config import AppConfig, ConfigError, load_config
from screening.config.models import Condition, field_path_exists
from screening.domain import Country, Language


def _edit_yaml(path: Path, edit) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    edit(data)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def test_repo_config_is_valid(app_config: AppConfig) -> None:
    assert set(app_config.regions) == {Country.ES, Country.MX}
    assert app_config.client.agent_name == "Sofía"
    assert [r.name for r in app_config.knockout_rules] == [
        "consent_given",
        "has_license",
        "license_matches_vehicle",
        "city_in_service_area",
    ]
    assert sum(c.weight for c in app_config.scoring.components) == app_config.scoring.max_score


def test_every_template_has_all_languages(app_config: AppConfig) -> None:
    for texts in app_config.templates.values():
        assert set(texts) == set(Language)


def test_scoring_weights_must_sum_max_score(config_dir: Path) -> None:
    _edit_yaml(
        config_dir / "scoring.yaml",
        lambda d: d["scoring"]["components"][0].update(weight=25),
    )

    with pytest.raises(ConfigError, match="pesos suman 90"):
        load_config(config_dir)


def test_rule_with_unknown_field_fails(config_dir: Path) -> None:
    def edit(data):
        data["rules"][1]["condition"]["field"] = "has_spaceship"

    _edit_yaml(config_dir / "rules" / "knockout.yaml", edit)

    with pytest.raises(ConfigError, match="has_spaceship"):
        load_config(config_dir)


def test_cadence_with_unknown_template_fails(config_dir: Path) -> None:
    def edit(data):
        data["cadences"][0]["steps"][1]["template"] = "does_not_exist"

    _edit_yaml(config_dir / "cadences.yaml", edit)

    with pytest.raises(ConfigError, match="does_not_exist"):
        load_config(config_dir)


def test_template_missing_language_fails(config_dir: Path) -> None:
    _edit_yaml(config_dir / "templates.yaml", lambda d: d["templates"]["first_contact"].pop("en"))

    with pytest.raises(ConfigError, match="first_contact"):
        load_config(config_dir)


def test_cadence_steps_must_be_ordered(config_dir: Path) -> None:
    def edit(data):
        data["cadences"][0]["steps"][1]["after_hours"] = 200

    _edit_yaml(config_dir / "cadences.yaml", edit)

    with pytest.raises(ConfigError, match="orden"):
        load_config(config_dir)


def test_missing_file_fails(config_dir: Path) -> None:
    (config_dir / "scoring.yaml").unlink()

    with pytest.raises(ConfigError, match="scoring.yaml"):
        load_config(config_dir)


@pytest.mark.parametrize(
    "raw",
    [
        {"field": "consent", "operator": "equals", "value": True, "all": []},
        {"field": "consent"},
        {"field": "availability", "operator": "in"},
        {"field": "license_years", "operator": "between", "min": 1},
        {"field": "consent", "operator": "equals"},
        {},
    ],
)
def test_invalid_condition_shapes(raw: dict) -> None:
    with pytest.raises(ValidationError):
        Condition.model_validate(raw)


def test_nested_condition_with_not_alias() -> None:
    condition = Condition.model_validate(
        {
            "all": [
                {"field": "has_license", "operator": "equals", "value": True},
                {"not": {"field": "work_permit", "operator": "equals", "value": False}},
                {"any": [{"special": "city_in_service_area"}]},
            ]
        }
    )

    assert condition.all is not None
    assert condition.all[1].not_ is not None


def test_equals_accepts_falsy_value() -> None:
    condition = Condition.model_validate({"field": "consent", "operator": "equals", "value": False})

    assert condition.value is False


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("city", True),
        ("delivery_experience.years", True),
        ("delivery_experience.salary", False),
        ("city.name", False),
        ("unknown", False),
    ],
)
def test_field_path_exists(path: str, expected: bool) -> None:
    assert field_path_exists(path) is expected
