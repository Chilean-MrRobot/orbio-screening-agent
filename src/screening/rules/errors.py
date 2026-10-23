"""Errores del motor de reglas: indican configuración o integración rota, no un candidato."""

from __future__ import annotations


class RulesEngineError(Exception):
    """Base de los errores del motor de reglas."""


class UnknownSpecialConditionError(RulesEngineError):
    """Una regla usa una condición especial que no está registrada."""


class MissingRegionError(RulesEngineError):
    """El país del candidato no tiene región en la configuración."""


class RuleEvaluationError(RulesEngineError):
    """Una condición no se puede evaluar (p. ej. compara tipos incompatibles)."""
