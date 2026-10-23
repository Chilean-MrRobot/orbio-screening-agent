"""Evaluador de `Condition` con lógica de tres valores (Kleene)."""

from __future__ import annotations

import operator as op
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from screening.config.models import Condition, RegionConfig
from screening.domain import ScreeningData
from screening.rules.errors import RuleEvaluationError
from screening.rules.specials import SpecialConditionRegistry
from screening.rules.values import Outcome, is_missing, resolve_field

_COMPARISONS: dict[str, Callable[[Any, Any], bool]] = {
    "equals": op.eq,
    "not_equals": op.ne,
    "greater_than": op.gt,
    "less_than": op.lt,
    "greater_or_equal": op.ge,
    "less_or_equal": op.le,
}


@dataclass(frozen=True)
class EvaluationContext:
    """Lo que una condición necesita además de la ficha: la región y las especiales."""

    region: RegionConfig
    registry: SpecialConditionRegistry


def evaluate_condition(
    condition: Condition, data: ScreeningData, context: EvaluationContext
) -> Outcome:
    if condition.field is not None:
        return _evaluate_field(condition, data)
    if condition.all is not None:
        return _evaluate_all([evaluate_condition(c, data, context) for c in condition.all])
    if condition.any is not None:
        return _evaluate_any([evaluate_condition(c, data, context) for c in condition.any])
    if condition.not_ is not None:
        inner = evaluate_condition(condition.not_, data, context)
        return inner if inner.is_unknown else Outcome(not inner.value)
    if condition.special is not None:
        return context.registry.get(condition.special)(data, context.region)
    raise RuleEvaluationError(f"condición vacía: {condition!r}")


def _evaluate_field(condition: Condition, data: ScreeningData) -> Outcome:
    path = condition.field or ""
    operator = condition.operator
    value = resolve_field(data, path)

    if operator == "exists":
        return Outcome(not is_missing(value))
    if operator == "not_exists":
        return Outcome(is_missing(value))
    if is_missing(value):
        return Outcome.unknown(path)

    try:
        if operator in _COMPARISONS:
            return Outcome(bool(_COMPARISONS[operator](value, condition.value)))
        if operator == "in":
            return Outcome(value in (condition.values or []))
        if operator == "not_in":
            return Outcome(value not in (condition.values or []))
        if operator == "between":
            return Outcome(bool(condition.min <= value <= condition.max))  # type: ignore[operator]
    except TypeError as e:
        raise RuleEvaluationError(
            f"no se puede evaluar {path!r} {operator} con el valor {value!r}: {e}"
        ) from e
    raise RuleEvaluationError(f"operador no soportado: {operator!r}")


def _evaluate_all(outcomes: list[Outcome]) -> Outcome:
    if any(o.value is False for o in outcomes):
        return Outcome(False)
    if all(o.value is True for o in outcomes):
        return Outcome(True)
    return _unknown_from(outcomes)


def _evaluate_any(outcomes: list[Outcome]) -> Outcome:
    if any(o.value is True for o in outcomes):
        return Outcome(True)
    if all(o.value is False for o in outcomes):
        return Outcome(False)
    return _unknown_from(outcomes)


def _unknown_from(outcomes: list[Outcome]) -> Outcome:
    return Outcome(None, frozenset().union(*(o.missing for o in outcomes if o.is_unknown)))
