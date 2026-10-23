from __future__ import annotations

from pathlib import Path

import pytest

from screening.adapters.llm_pricing import ModelPrice, PricingTable, load_pricing
from screening.config.loader import ConfigError


@pytest.fixture
def table() -> PricingTable:
    return PricingTable(
        models={
            "gpt-5-mini": ModelPrice(
                input_per_million=0.25, cached_input_per_million=0.025, output_per_million=2.0
            ),
            "gpt-5": ModelPrice(input_per_million=1.25, output_per_million=10.0),
        }
    )


def test_loads_repo_pricing_with_configured_models(app_config) -> None:
    pricing = load_pricing()

    for model in (
        app_config.llm.conversation_model,
        app_config.llm.extraction_model,
        app_config.llm.classifier_model,
        app_config.llm.judge_model,
        app_config.llm.embedding_model,
    ):
        assert pricing.price_for(model) is not None, model


def test_cost_adds_input_and_output_per_million(table: PricingTable) -> None:
    assert table.cost("gpt-5-mini", 1_000_000, 1_000_000) == pytest.approx(2.25)


def test_cached_tokens_use_cached_price(table: PricingTable) -> None:
    cost = table.cost("gpt-5-mini", 1_000_000, 0, cached_input_tokens=400_000)

    assert cost == pytest.approx(0.6 * 0.25 + 0.4 * 0.025)


def test_cached_tokens_without_cached_price_use_input_price(table: PricingTable) -> None:
    cost = table.cost("gpt-5", 1_000_000, 0, cached_input_tokens=500_000)

    assert cost == pytest.approx(1.25)


def test_dated_model_uses_longest_prefix(table: PricingTable) -> None:
    assert table.price_for("gpt-5-mini-2025-08-07") == table.models["gpt-5-mini"]
    assert table.price_for("gpt-5-2025-08-07") == table.models["gpt-5"]


def test_unknown_model_costs_zero(table: PricingTable, caplog) -> None:
    assert table.price_for("claude-x") is None
    assert table.cost("claude-x", 1000, 1000) == 0.0
    assert "claude-x" in caplog.text


def test_invalid_pricing_file_raises_config_error(tmp_path: Path) -> None:
    path = tmp_path / "llm_pricing.yaml"
    path.write_text(
        "pricing:\n  models:\n    gpt-5-mini:\n      input_per_million: -1\n", encoding="utf-8"
    )

    with pytest.raises(ConfigError):
        load_pricing(path)


def test_missing_pricing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_pricing(tmp_path / "missing.yaml")


def test_reads_from_config_dir_env(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "llm_pricing.yaml").write_text(
        "pricing:\n  models:\n    m:\n      input_per_million: 1\n      output_per_million: 2\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SCREENING_CONFIG_DIR", str(tmp_path))

    assert load_pricing().models == {"m": ModelPrice(input_per_million=1, output_per_million=2)}
