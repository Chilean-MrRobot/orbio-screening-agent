"""Resultado de tres valores y lectura de campos de la ficha."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from screening.domain import ScreeningData


@dataclass(frozen=True)
class Outcome:
    """Resultado de una condición: verdadero, falso o desconocido (`value=None`).

    `missing` lista los campos que harían falta para decidir; solo se llena si es desconocido.
    """

    value: bool | None
    missing: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def unknown(cls, *fields: str) -> Outcome:
        return cls(None, frozenset(fields))

    @property
    def is_unknown(self) -> bool:
        return self.value is None


def resolve_field(data: ScreeningData, path: str) -> Any:
    """Lee una ruta con punto (`delivery_experience.years`); `None` si falta algún tramo."""
    current: Any = data
    for part in path.split("."):
        if current is None:
            return None
        if not isinstance(current, BaseModel):
            raise AttributeError(f"{path!r} no es una ruta válida")
        current = getattr(current, part)
    return current


def is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())
