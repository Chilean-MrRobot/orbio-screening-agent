"""Esquema de salida del turno de extracción y su validación contra `ScreeningData`."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from pydantic import BaseModel, ValidationError

from screening.domain import Availability, Language, Schedule, ScreeningData, Sentiment, VehicleType
from screening.ports import ExtractionOutcome

# Campos que puede llenar la conversación; `needs_review` lo decide el orquestador.
EXTRACTABLE_FIELDS: tuple[str, ...] = tuple(
    name for name in ScreeningData.model_fields if name != "needs_review"
)
# Convención con el orquestador (008): en `closing` se espera este campo y, si el candidato
# confirma el resumen sin corregir nada, va en `provided_fields` (no existe en ScreeningData).
CONFIRMATION_FIELD = "summary_confirmed"


class ExtractedExperience(BaseModel):
    years: float
    platforms: list[str]


class ExtractedFields(BaseModel):
    """Lo que dijo el candidato en los mensajes nuevos; `null` si no lo dijo.

    Sin defaults a propósito: el modo estricto de OpenAI exige todas las propiedades.
    """

    full_name: str | None
    consent: bool | None
    has_license: bool | None
    license_type: str | None
    license_years: float | None
    vehicle_type: VehicleType | None
    owns_vehicle: bool | None
    city: str | None
    availability: Availability | None
    preferred_schedule: Schedule | None
    delivery_experience: ExtractedExperience | None
    start_date: str | None
    work_permit: bool | None


class ExtractionResponse(BaseModel):
    """Salida estructurada que se le pide al LLM de extracción."""

    fields: ExtractedFields
    invalid_fields: list[str]
    language: Language
    sentiment: Sentiment
    opt_out: bool
    human_requested: bool
    question: str | None
    summary_confirmed: bool


def build_outcome(
    response: ExtractionResponse,
    *,
    licenses: Collection[str] | None = None,
    expected_fields: Collection[str] = (),
    current: ScreeningData | None = None,
) -> ExtractionOutcome:
    """Valida campo por campo: lo que no valida pasa a `invalid_fields` en vez de fallar todo.

    `licenses` son los tipos válidos del país; un `license_type` fuera de la tabla es inválido.
    Un campo con el mismo valor que ya tiene la ficha (`current`) no es una corrección: el
    modelo a veces repite datos del resumen al leer "sí, todo correcto". La confirmación del
    resumen solo cuenta si se esperaba y no vino con correcciones reales.
    """
    invalid = {name for name in response.invalid_fields if name in EXTRACTABLE_FIELDS}
    values: dict[str, Any] = {}
    for name, value in response.fields.model_dump(exclude_none=True).items():
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        if name == "license_type" and licenses is not None:
            value = _match_license(value, licenses)
            if value is None:
                invalid.add(name)
                continue
        try:
            parsed = ScreeningData.model_validate({name: value})
        except ValidationError:
            invalid.add(name)
            continue
        if current is not None and getattr(parsed, name) == getattr(current, name):
            continue  # repite lo que ya está en la ficha: no es un dato nuevo ni una corrección
        values[name] = value

    updates = ScreeningData.model_validate(values)
    provided = [name for name in EXTRACTABLE_FIELDS if name in values]
    if response.summary_confirmed and CONFIRMATION_FIELD in expected_fields and not values:
        provided.append(CONFIRMATION_FIELD)
    return ExtractionOutcome(
        updates=updates,
        provided_fields=provided,
        invalid_fields=[n for n in EXTRACTABLE_FIELDS if n in invalid and n not in values],
        language=response.language,
        sentiment=response.sentiment,
        opt_out=response.opt_out,
        human_requested=response.human_requested,
        question=(response.question or "").strip() or None,
    )


def _match_license(value: str, licenses: Collection[str]) -> str | None:
    """El código tal como está en la tabla del país, sin importar mayúsculas."""
    by_lower = {code.lower(): code for code in licenses}
    return by_lower.get(value.lower())
