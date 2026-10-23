"""Cálculo del puntaje: función pura y determinista, nunca LLM.

Ver `specs/004-puntaje.md`.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any

from screening.config.models import ScoringBand, ScoringComponent, ScoringConfig
from screening.domain import ScoreBreakdown, ScreeningData


def compute_score(data: ScreeningData, config: ScoringConfig, today: date) -> ScoreBreakdown:
    """Suma `weight × value` por componente. Un campo vacío o sin calce aporta 0.

    `today` lo entrega quien llama a partir del `Clock` inyectado.
    """
    points = {c.name: c.weight * _component_value(c, data, today) for c in config.components}
    return ScoreBreakdown(
        total=_round(sum(points.values()), "0.1"),
        components={name: _round(p, "0.01") for name, p in points.items()},
    )


def _component_value(component: ScoringComponent, data: ScreeningData, today: date) -> float:
    raw = _resolve(data, component.field)
    match component.type:
        case "bands":
            return _band_value(component.bands or [], _as_number(raw))
        case "mapping":
            return (component.mapping or {}).get(_as_key(raw), 0.0)
        case "boolean":
            return 1.0 if raw is True else 0.0
        case "days_until":
            return _band_value(component.bands or [], _days_until(raw, today))


def _resolve(data: ScreeningData, path: str) -> Any:
    """Sigue rutas como `delivery_experience.years`; un tramo vacío da `None`."""
    value: Any = data
    for part in path.split("."):
        if value is None:
            return None
        value = getattr(value, part, None)
    return value


def _band_value(bands: list[ScoringBand], x: float | None) -> float:
    if x is None:
        return 0.0
    for band in bands:
        if (band.min is None or band.min <= x) and (band.max is None or x <= band.max):
            return band.value
    return 0.0


def _as_number(raw: Any) -> float | None:
    # bool es subclase de int: un sí/no no es una cantidad.
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    return float(raw)


def _as_key(raw: Any) -> str | None:
    if isinstance(raw, Enum):
        return str(raw.value)
    return raw if isinstance(raw, str) else None


def _days_until(raw: Any, today: date) -> float | None:
    if isinstance(raw, datetime):
        raw = raw.date()
    if not isinstance(raw, date):
        return None
    # Una fecha pasada significa que puede empezar ya.
    return float(max((raw - today).days, 0))


def _round(value: float, step: str) -> float:
    """Redondeo hacia arriba en el medio (78,25 → 78,3), no el bancario de `round()`."""
    return float(Decimal(str(value)).quantize(Decimal(step), rounding=ROUND_HALF_UP))
