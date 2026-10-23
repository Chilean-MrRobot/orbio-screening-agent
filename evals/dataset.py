"""Carga de golden datasets: un JSONL por suite en `evals/datasets/`."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from evals.models import EvalCase

DATASETS_DIR = Path(__file__).resolve().parent / "datasets"


class DatasetError(Exception):
    """El dataset no existe o tiene una línea inválida."""


def load_suite(name: str, datasets_dir: Path | str | None = None) -> list[EvalCase]:
    path = Path(datasets_dir or DATASETS_DIR) / f"{name}.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as e:
        raise DatasetError(f"no existe la suite {name!r} ({path})") from e

    cases: list[EvalCase] = []
    seen: set[str] = set()
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            case = EvalCase.model_validate(json.loads(line))
        except (json.JSONDecodeError, ValidationError) as e:
            raise DatasetError(f"{path}, línea {number}: {e}") from e
        if case.id in seen:
            raise DatasetError(f"{path}, línea {number}: id repetido {case.id!r}")
        seen.add(case.id)
        cases.append(case)
    return cases
