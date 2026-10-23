"""Evaluación incremental de las reglas eliminatorias. Ver `specs/003-motor-reglas.md`."""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import BaseModel, Field

from screening.config import AppConfig
from screening.config.models import Condition, KnockoutRule
from screening.domain import Country, KnockoutResult, ScreeningData
from screening.rules.conditions import EvaluationContext, evaluate_condition
from screening.rules.errors import MissingRegionError
from screening.rules.specials import SpecialConditionRegistry, default_registry
from screening.rules.values import is_missing, resolve_field


class PendingRule(BaseModel):
    name: str
    missing_fields: list[str]


class KnockoutEvaluation(BaseModel):
    """Estado de las eliminatorias tras la última respuesta del candidato."""

    result: KnockoutResult | None = Field(
        description="Veredicto del contrato; `None` mientras queden reglas pendientes."
    )
    passed_rules: list[str] = Field(default_factory=list)
    pending_rules: list[PendingRule] = Field(default_factory=list)
    message_template: str | None = Field(
        default=None, description="Plantilla del mensaje de descarte de la regla que falló."
    )


def evaluate_knockouts(
    data: ScreeningData,
    country: Country,
    config: AppConfig,
    registry: SpecialConditionRegistry | None = None,
) -> KnockoutEvaluation:
    region = config.regions.get(country)
    if region is None:
        raise MissingRegionError(
            f"el país {country!s} no tiene región configurada "
            f"(configuradas: {sorted(str(c) for c in config.regions)})"
        )
    registry = registry or default_registry()
    rules = [r for r in config.knockout_rules if r.enabled]
    _check_specials(rules, registry)

    context = EvaluationContext(region=region, registry=registry)
    passed: list[str] = []
    pending: list[PendingRule] = []

    for rule in rules:
        absent = [f for f in rule.requires if is_missing(resolve_field(data, f))]
        if absent:
            pending.append(PendingRule(name=rule.name, missing_fields=sorted(absent)))
            continue

        outcome = evaluate_condition(rule.condition, data, context)
        if outcome.value is True:
            passed.append(rule.name)
        elif outcome.value is None:
            pending.append(PendingRule(name=rule.name, missing_fields=sorted(outcome.missing)))
        else:
            return KnockoutEvaluation(
                result=KnockoutResult(
                    passed=False, failed_rule=rule.name, reason_code=rule.reason_code
                ),
                passed_rules=passed,
                pending_rules=pending,
                message_template=rule.message_template,
            )

    result = None if pending else KnockoutResult(passed=True)
    return KnockoutEvaluation(result=result, passed_rules=passed, pending_rules=pending)


def _check_specials(rules: list[KnockoutRule], registry: SpecialConditionRegistry) -> None:
    """Falla de inmediato si una regla usa una especial sin registrar, aunque esté pendiente."""
    for rule in rules:
        for condition in _walk(rule.condition):
            if condition.special is not None:
                registry.get(condition.special)


def _walk(condition: Condition) -> Iterator[Condition]:
    yield condition
    for child in (condition.all or []) + (condition.any or []):
        yield from _walk(child)
    if condition.not_ is not None:
        yield from _walk(condition.not_)
