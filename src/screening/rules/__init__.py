"""Motor de reglas eliminatorias (determinista, nunca LLM)."""

from screening.rules.conditions import EvaluationContext, evaluate_condition
from screening.rules.engine import KnockoutEvaluation, PendingRule, evaluate_knockouts
from screening.rules.errors import (
    MissingRegionError,
    RuleEvaluationError,
    RulesEngineError,
    UnknownSpecialConditionError,
)
from screening.rules.specials import (
    SpecialConditionFn,
    SpecialConditionRegistry,
    city_in_service_area,
    default_registry,
    license_compatible,
    normalize_text,
)
from screening.rules.values import Outcome, is_missing, resolve_field

__all__ = [
    "EvaluationContext",
    "KnockoutEvaluation",
    "MissingRegionError",
    "Outcome",
    "PendingRule",
    "RuleEvaluationError",
    "RulesEngineError",
    "SpecialConditionFn",
    "SpecialConditionRegistry",
    "UnknownSpecialConditionError",
    "city_in_service_area",
    "default_registry",
    "evaluate_condition",
    "evaluate_knockouts",
    "is_missing",
    "license_compatible",
    "normalize_text",
    "resolve_field",
]
