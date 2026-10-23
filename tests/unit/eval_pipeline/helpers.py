"""Utilidades para los tests del pipeline de evaluación."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evals.models import EvalCase, JudgeVerdict


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", "utf-8")
    return path


def make_case(case_id: str = "c1", expected_verdict: str | None = None, **input: Any) -> EvalCase:
    metadata = {"expected_verdict": expected_verdict} if expected_verdict else {}
    return EvalCase(
        id=case_id,
        input=input or {"question": "¿Puedo trabajar solo fines de semana?"},
        expected="Sí, hay turnos de fin de semana.",
        metadata=metadata,
    )


def verdict(value: str = "pass", score: int = 5) -> JudgeVerdict:
    return JudgeVerdict(reasoning="motivo", verdict=value, score=score)
