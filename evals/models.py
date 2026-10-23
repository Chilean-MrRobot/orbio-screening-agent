"""Modelos del pipeline de evaluación: casos, veredictos, resultados y reporte."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from screening.ports import LLMUsage

Verdict = Literal["pass", "fail"]
VERDICTS: tuple[Verdict, ...] = ("pass", "fail")


class EvalCase(BaseModel):
    """Una línea del golden dataset."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    input: dict[str, Any]
    expected: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def _check_expected_verdict(cls, metadata: dict[str, Any]) -> dict[str, Any]:
        if metadata.get("expected_verdict", "pass") not in VERDICTS:
            raise ValueError("metadata.expected_verdict debe ser 'pass' o 'fail'")
        return metadata

    @property
    def expected_verdict(self) -> Verdict:
        return self.metadata.get("expected_verdict", "pass")


@dataclass(frozen=True)
class TargetOutput:
    """Lo que devuelve el sistema evaluado para un caso."""

    answer: str
    usage: LLMUsage | None = None


Target = Callable[[EvalCase], TargetOutput]


class JudgeVerdict(BaseModel):
    """Salida estructurada del juez. `reasoning` va primero para que razone antes de decidir."""

    reasoning: str
    verdict: Verdict
    score: Literal[1, 2, 3, 4, 5]


class CaseResult(BaseModel):
    case_id: str
    input: dict[str, Any]
    expected: str
    expected_verdict: Verdict
    answer: str | None
    verdict: Verdict
    score: int | None = None
    reasoning: str | None = None
    correct: bool
    latency_ms: float | None
    target_usage: LLMUsage | None = None
    judge_usage: LLMUsage | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class UsageTotals(BaseModel):
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class SuiteMetrics(BaseModel):
    total: int
    correct: int
    passed: int
    errors: int
    accuracy: float
    pass_rate: float
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    target: UsageTotals
    judge: UsageTotals
    total_cost_usd: float


class SuiteReport(BaseModel):
    suite: str
    started_at: datetime
    finished_at: datetime
    judge_model: str
    judge_prompt_version: str
    metrics: SuiteMetrics
    cases: list[CaseResult]
