"""Reloj inyectable: el código nunca llama a `datetime.now()` directamente."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Reloj controlable para tests de cadencias y fechas."""

    def __init__(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("FixedClock requiere un datetime con zona horaria")
        self._at = at

    def now(self) -> datetime:
        return self._at

    def advance(self, **delta: float) -> None:
        self._at += timedelta(**delta)
