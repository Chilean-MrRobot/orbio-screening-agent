"""Normalización de teléfonos a E.164 con los prefijos de las regiones del YAML."""

from __future__ import annotations

import re
from dataclasses import dataclass

import phonenumbers

from screening.config.models import RegionConfig
from screening.domain import Country
from screening.intake.models import IntakeReason


@dataclass(frozen=True)
class PhoneResult:
    e164: str | None = None
    country: Country | None = None
    reason: IntakeReason | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.e164 is not None


def normalize_phone(
    raw: str, country: Country | None, regions: dict[Country, RegionConfig]
) -> PhoneResult:
    """Devuelve el teléfono en E.164 y el país al que pertenece según su prefijo.

    En formato internacional (`+…` o `00…`) el país sale del prefijo. En formato local
    se usa `country`; si no viene, se acepta si es válido en exactamente una región.
    """
    compact = re.sub(r"[^\d+]", "", raw)
    if compact.startswith("00"):
        compact = "+" + compact[2:]

    if compact.startswith("+"):
        number = _parse(compact, None)
        if number is None:
            return _invalid(raw)
        prefixes = {region.phone_prefix: code for code, region in regions.items()}
        phone_country = prefixes.get(f"+{number.country_code}")
        if phone_country is None:
            return PhoneResult(
                reason=IntakeReason.COUNTRY_NOT_SERVED,
                error=f"phone: prefijo +{number.country_code} de un país sin servicio",
            )
        return _ok(number, phone_country)

    if country is not None:
        number = _parse(compact, country)
        return _ok(number, country) if number is not None else _invalid(raw)

    matches = [(code, n) for code in regions if (n := _parse(compact, code)) is not None]
    if not matches:
        return _invalid(raw)
    if len(matches) > 1:
        return PhoneResult(
            reason=IntakeReason.COUNTRY_UNDETERMINED,
            error=f"phone: {raw!r} es válido en {', '.join(c for c, _ in matches)}",
        )
    code, number = matches[0]
    return _ok(number, code)


def _parse(text: str, region: Country | None) -> phonenumbers.PhoneNumber | None:
    """Devuelve el número solo si `phonenumbers` lo considera válido."""
    try:
        number = phonenumbers.parse(text, region.value if region else None)
    except phonenumbers.NumberParseException:
        return None
    return number if phonenumbers.is_valid_number(number) else None


def _ok(number: phonenumbers.PhoneNumber, country: Country) -> PhoneResult:
    e164 = phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
    return PhoneResult(e164=e164, country=country)


def _invalid(raw: str) -> PhoneResult:
    return PhoneResult(reason=IntakeReason.PHONE_INVALID, error=f"phone: {raw!r} no es válido")
