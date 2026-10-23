"""Registro de suites: qué objetivo evalúa cada dataset y con qué umbral de accuracy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from evals.models import EvalCase, Target, TargetOutput
from screening.config import AppConfig
from screening.ports import LLMClient


@dataclass(frozen=True)
class SuiteSpec:
    description: str
    make_target: Callable[[LLMClient, AppConfig], Target]
    threshold: float


def _given_answer(llm: LLMClient, config: AppConfig) -> Target:
    """La respuesta ya viene en el caso: lo que se evalúa es al juez."""

    def target(case: EvalCase) -> TargetOutput:
        return TargetOutput(answer=str(case.input["answer"]))

    return target


SUITES: dict[str, SuiteSpec] = {
    "judge_sanity": SuiteSpec(
        description="Valida al juez: paráfrasis deben pasar y contradicciones fallar",
        make_target=_given_answer,
        threshold=0.9,
    ),
}
