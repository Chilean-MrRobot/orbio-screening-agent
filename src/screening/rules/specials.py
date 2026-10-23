"""Condiciones especiales: funciones del motor para lo que no se expresa con un operador.

Se registran por nombre y las reglas del YAML las referencian con `special: <nombre>`.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable

from screening.config.models import RegionConfig
from screening.domain import ScreeningData
from screening.rules.errors import UnknownSpecialConditionError
from screening.rules.values import Outcome, is_missing

SpecialConditionFn = Callable[[ScreeningData, RegionConfig], Outcome]


class SpecialConditionRegistry:
    def __init__(self) -> None:
        self._functions: dict[str, SpecialConditionFn] = {}

    def register(self, name: str, fn: SpecialConditionFn) -> None:
        self._functions[name] = fn

    def get(self, name: str) -> SpecialConditionFn:
        try:
            return self._functions[name]
        except KeyError:
            known = sorted(self._functions) or "ninguna"
            raise UnknownSpecialConditionError(
                f"condición especial no registrada: {name!r} (registradas: {known})"
            ) from None

    def __contains__(self, name: object) -> bool:
        return name in self._functions


def normalize_text(text: str) -> str:
    """Sin mayúsculas, sin tildes y con los espacios colapsados."""
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(without_marks.casefold().split())


def license_compatible(data: ScreeningData, region: RegionConfig) -> Outcome:
    """La licencia habilita el vehículo según la tabla de la región (y su antigüedad mínima)."""
    missing = [f for f in ("license_type", "vehicle_type") if is_missing(getattr(data, f))]
    if missing:
        return Outcome.unknown(*missing)

    wanted = (data.license_type or "").strip().casefold()
    spec = next((s for name, s in region.licenses.items() if name.casefold() == wanted), None)
    if spec is None or data.vehicle_type not in spec.vehicles:
        return Outcome(False)

    min_years = spec.min_years.get(data.vehicle_type)  # type: ignore[arg-type]
    if min_years is None:
        return Outcome(True)
    if data.license_years is None:
        return Outcome.unknown("license_years")
    return Outcome(data.license_years >= min_years)


def city_in_service_area(data: ScreeningData, region: RegionConfig) -> Outcome:
    """La ciudad coincide con el nombre o un alias de una ciudad de servicio."""
    if is_missing(data.city):
        return Outcome.unknown("city")
    wanted = normalize_text(data.city or "")
    for city in region.cities:
        if any(normalize_text(n) == wanted for n in (city.name, *city.aliases)):
            return Outcome(True)
    return Outcome(False)


def default_registry() -> SpecialConditionRegistry:
    registry = SpecialConditionRegistry()
    registry.register("license_compatible", license_compatible)
    registry.register("city_in_service_area", city_in_service_area)
    return registry
