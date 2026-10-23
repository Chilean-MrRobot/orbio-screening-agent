"""Precios de los modelos (`config/llm_pricing.yaml`) para calcular el costo de cada llamada."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from screening.config.loader import DEFAULT_CONFIG_DIR, ConfigError

logger = logging.getLogger(__name__)

PRICING_FILE = "llm_pricing.yaml"
_PER_MILLION = 1_000_000


class ModelPrice(BaseModel):
    """USD por millón de tokens."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_per_million: float = Field(ge=0)
    output_per_million: float = Field(ge=0)
    cached_input_per_million: float | None = Field(default=None, ge=0)


class PricingTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models: dict[str, ModelPrice]

    def price_for(self, model: str) -> ModelPrice | None:
        """Nombre exacto o, si no está, el prefijo más largo (para modelos con fecha)."""
        if model in self.models:
            return self.models[model]
        prefixes = [name for name in self.models if model.startswith(f"{name}-")]
        return self.models[max(prefixes, key=len)] if prefixes else None

    def cost(
        self, model: str, input_tokens: int, output_tokens: int, cached_input_tokens: int = 0
    ) -> float:
        price = self.price_for(model)
        if price is None:
            logger.warning("sin precio para el modelo %s; se registra costo 0", model)
            return 0.0
        cached_price = price.cached_input_per_million
        if cached_price is None:
            cached_price = price.input_per_million
        cached = min(cached_input_tokens, input_tokens)
        return (
            (input_tokens - cached) * price.input_per_million
            + cached * cached_price
            + output_tokens * price.output_per_million
        ) / _PER_MILLION


def load_pricing(path: Path | str | None = None) -> PricingTable:
    if path is None:
        base = Path(os.environ.get("SCREENING_CONFIG_DIR") or DEFAULT_CONFIG_DIR)
        path = base / PRICING_FILE
    path = Path(path)
    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise ConfigError(f"falta la tabla de precios: {path}") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"YAML inválido en {path}: {e}") from e
    if not isinstance(content, dict) or "pricing" not in content:
        raise ConfigError(f"{path} debe tener la clave raíz 'pricing'")
    try:
        return PricingTable.model_validate(content["pricing"])
    except ValidationError as e:
        raise ConfigError(f"tabla de precios inválida en {path}:\n{e}") from e
