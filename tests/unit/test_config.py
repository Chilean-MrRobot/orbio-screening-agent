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


# --- Agendamiento (spec 021) ------------------------------------------------------


def test_repo_scheduling_config_is_valid(app_config: AppConfig) -> None:
    scheduling = app_config.scheduling
    assert scheduling is not None and scheduling.enabled
    assert scheduling.options_per_offer == 3 and scheduling.min_notice_hours == 24
    assert set(scheduling.countries) == {Country.ES, Country.MX}
    assert set(scheduling.countries[Country.MX].locations) == {
        c.name for c in app_config.regions[Country.MX].cities
    }


def test_without_scheduling_file_it_is_disabled(config_dir: Path) -> None:
    (config_dir / "scheduling.yaml").unlink()

    assert load_config(config_dir).scheduling is None


def test_in_person_scheduling_needs_every_city_location(config_dir: Path) -> None:
    _edit_yaml(
        config_dir / "scheduling.yaml",
        lambda d: d["scheduling"]["countries"]["MX"]["locations"].pop("Puebla"),
    )

    with pytest.raises(ConfigError, match="sin dirección: \['Puebla'\]"):
        load_config(config_dir)


def test_scheduling_window_must_fit_a_slot(config_dir: Path) -> None:
    def edit(data):
        data["scheduling"]["countries"]["ES"]["windows"] = [{"start": "10:00", "end": "10:15"}]

    _edit_yaml(config_dir / "scheduling.yaml", edit)

    with pytest.raises(ConfigError, match="no alcanza"):
        load_config(config_dir)


def test_scheduling_window_must_start_before_it_ends(config_dir: Path) -> None:
    def edit(data):
        data["scheduling"]["countries"]["ES"]["windows"] = [{"start": "13:00", "end": "10:00"}]

    _edit_yaml(config_dir / "scheduling.yaml", edit)

    with pytest.raises(ConfigError, match="empezar antes de terminar"):
        load_config(config_dir)


def test_scheduling_templates_must_exist(config_dir: Path) -> None:
    _edit_yaml(config_dir / "templates.yaml", lambda d: d["templates"].pop("interview_confirmed"))

    with pytest.raises(ConfigError, match="interview_confirmed"):
        load_config(config_dir)


def test_scheduling_reengagement_templates_must_be_cadence_templates(config_dir: Path) -> None:
    _edit_yaml(
        config_dir / "scheduling.yaml",
        lambda d: d["scheduling"]["reengagement_templates"].update(nope="interview_reminder"),
    )

    with pytest.raises(ConfigError, match="nope"):
        load_config(config_dir)


def test_scheduling_country_needs_a_region(config_dir: Path) -> None:
    (config_dir / "regions" / "mx.yaml").unlink()

    with pytest.raises(ConfigError, match="MX, que no tiene región"):
        load_config(config_dir)
