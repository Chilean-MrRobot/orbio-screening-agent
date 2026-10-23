"""Objetivo de la suite `summary` (spec 012): ficha + conversación → resumen para el recruiter.

`input`: lo de `candidate_from_input` (`today`, `country`, `name`, `city`, `language`,
`sentiment`, `data`, `history`, `human_requested`) más `status`, `knockout`, `score` y
`events` (`[{"type": ..., "detail": {...}}]`). La respuesta es el texto de `candidate.summary`.
"""

from __future__ import annotations

from evals.models import EvalCase, Target, TargetOutput
from evals.targets.common import candidate_from_input, clock_for
from screening.config import AppConfig
from screening.domain import Candidate, CandidateStatus, KnockoutResult, ScoreBreakdown
from screening.ports import LLMClient
from screening.summary import SummaryService


def make_target(llm: LLMClient, config: AppConfig) -> Target:
    service = SummaryService(llm, config)

    def target(case: EvalCase) -> TargetOutput:
        candidate = summary_candidate(case)
        summary, usage = service.summarize(candidate)
        if summary.error:
            raise RuntimeError(summary.error)  # que cuente como error del caso, no como pass
        return TargetOutput(answer=summary.render(), usage=usage[0] if usage else None)

    return target


def summary_candidate(case: EvalCase) -> Candidate:
    data = case.input
    candidate = candidate_from_input(data)
    candidate.status = CandidateStatus(data["status"])
    if "knockout" in data:
        candidate.knockout = KnockoutResult.model_validate(data["knockout"])
    if "score" in data:
        candidate.score = ScoreBreakdown.model_validate(data["score"])
    now = clock_for(data).now()
    for event in data.get("events", []):
        candidate.record_event(now, event["type"], **event.get("detail", {}))
    return candidate
