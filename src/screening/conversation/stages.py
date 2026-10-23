"""Máquina de estados: qué campos faltan por etapa y en qué etapa va la ficha.

Los campos salen de las reglas (`requires` por `stage`), de los componentes del puntaje y
de la lista de campos del diseño. Ver `specs/008-orquestador.md`.
"""

from __future__ import annotations

from screening.config import AppConfig
from screening.domain import Candidate, Stage
from screening.rules import KnockoutEvaluation

# Pseudo-campo del cierre: el agente lo pone en `provided_fields` cuando el candidato
# confirma el resumen sin cambios.
CONFIRMATION_FIELD = "summary_confirmed"

# Campos "de dato" del diseño (§4) que no salen de reglas ni del puntaje.
DESIGN_FIELDS: dict[Stage, tuple[str, ...]] = {
    Stage.WELCOME: ("full_name",),
    Stage.PROFILE: ("work_permit",),
}

QUESTION_STAGES = (Stage.WELCOME, Stage.KNOCKOUT, Stage.PROFILE)


def stage_fields(stage: Stage, config: AppConfig) -> list[str]:
    """Campos de la etapa, en el orden a preguntar (sin los dinámicos del motor)."""
    if stage not in QUESTION_STAGES:
        return []
    design = list(DESIGN_FIELDS.get(stage, ()))
    rules = _rule_fields(stage, config)
    if stage != Stage.PROFILE:
        return _unique([*design, *rules])
    # El puntaje alimenta el perfil, salvo los campos que ya pidió una etapa anterior.
    earlier = {f for s in (Stage.WELCOME, Stage.KNOCKOUT) for f in stage_fields(s, config)}
    scoring = [_top(c.field) for c in config.scoring.components]
    return _unique([f for f in [*rules, *scoring] if f not in earlier] + design)


def missing_fields(
    candidate: Candidate,
    stage: Stage,
    evaluation: KnockoutEvaluation | None,
    config: AppConfig,
) -> list[str]:
    """Campos de la etapa sin valor y fuera de `needs_review`, en el orden a preguntar."""
    fields = stage_fields(stage, config)
    if evaluation is not None:
        stages = {r.name: r.stage for r in config.knockout_rules}
        for pending in evaluation.pending_rules:
            if stages.get(pending.name) == stage:
                fields.extend(_top(f) for f in pending.missing_fields)
    data = candidate.data
    return [
        f for f in _unique(fields) if getattr(data, f, None) is None and f not in data.needs_review
    ]


def current_stage(
    candidate: Candidate, evaluation: KnockoutEvaluation | None, config: AppConfig
) -> Stage:
    """La primera etapa con campos faltantes; si no falta nada, el cierre."""
    for stage in QUESTION_STAGES:
        if missing_fields(candidate, stage, evaluation, config):
            return stage
    return Stage.CLOSING


def _rule_fields(stage: Stage, config: AppConfig) -> list[str]:
    return [
        _top(f) for r in config.knockout_rules if r.enabled and r.stage == stage for f in r.requires
    ]


def _top(path: str) -> str:
    return path.split(".", 1)[0]


def _unique(fields: list[str]) -> list[str]:
    return list(dict.fromkeys(fields))
