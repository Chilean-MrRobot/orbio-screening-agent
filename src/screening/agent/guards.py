"""Filtro de fugas: el mensaje al candidato nunca muestra identificadores internos."""

from __future__ import annotations

import re
from enum import StrEnum

from screening.domain import Availability, Schedule, ScreeningData, Stage, VehicleType

FAQ_TOOL_NAME = "search_faq"


def _internal_terms() -> set[str]:
    # Solo los identificadores con guion bajo: "city" o "consent" son palabras normales.
    fields = {name for name in ScreeningData.model_fields if "_" in name}
    enums: list[type[StrEnum]] = [Availability, Schedule, VehicleType, Stage]
    values = {member.value for enum in enums for member in enum if "_" in member.value}
    request_fields = {"ask_fields", "retry_fields", "closing_summary", "expected_fields"}
    return fields | values | request_fields | {FAQ_TOOL_NAME}


_LEAK = re.compile(
    r"\b(" + "|".join(sorted(map(re.escape, _internal_terms()))) + r")\b|\{\{|\}\}",
    re.IGNORECASE,
)


def find_internal_leak(text: str) -> str | None:
    """El primer identificador interno que aparece en el texto, o `None` si está limpio."""
    match = _LEAK.search(text)
    return match.group(0) if match else None
