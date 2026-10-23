"""Etapa 0 del proceso: del JSON del portal a la ficha del candidato. Ver `specs/002-intake.md`."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from screening.clock import Clock
from screening.config import AppConfig
from screening.domain import ApplicationPayload, Candidate, CandidateStatus, ScreeningData
from screening.intake.models import IntakeOutcome, IntakeReason, IntakeResult
from screening.intake.phone import PhoneResult, normalize_phone
from screening.ports import CandidateRepository

REQUIRED_FIELDS = ("application_id", "source")


def process_application(
    raw: dict[str, Any],
    *,
    repository: CandidateRepository,
    clock: Clock,
    config: AppConfig,
) -> IntakeResult:
    now = clock.now()
    application, ignored, errors = _parse_payload(raw)
    if application is None:
        return IntakeResult(
            outcome=IntakeOutcome.REJECTED, reason=IntakeReason.INVALID_PAYLOAD, errors=errors
        )

    if application.phone is None:
        phone = PhoneResult(reason=IntakeReason.PHONE_MISSING, error="phone: falta el teléfono")
    else:
        phone = normalize_phone(application.phone, application.country, config.regions)

    if phone.e164 is not None:
        existing = repository.get_by_phone(phone.e164)
        if existing is not None:
            existing.record_event(
                now,
                "duplicate_application",
                application_id=application.application_id,
                source=application.source,
                vacancy_id=application.vacancy_id,
                applied_at=application.applied_at,
            )
            repository.save(existing)
            return IntakeResult(outcome=IntakeOutcome.DUPLICATE, candidate=existing)

    candidate = Candidate(
        application=application,
        phone=phone.e164,
        country=application.country or phone.country,
        data=ScreeningData(full_name=application.full_name),
        created_at=now,
        updated_at=now,
    )
    candidate.record_event(
        now,
        "application_received",
        application_id=application.application_id,
        source=application.source,
        email=application.email,
    )
    if ignored:
        candidate.record_event(now, "fields_ignored", fields=ignored)
    if application.country and phone.country and application.country != phone.country:
        candidate.record_event(
            now,
            "country_mismatch",
            payload_country=application.country,
            phone_country=phone.country,
        )

    if not phone.ok:
        candidate.set_status(CandidateStatus.INVALID_DATA, now, reason=phone.reason)
        repository.save(candidate)
        return IntakeResult(
            outcome=IntakeOutcome.INVALID_DATA,
            candidate=candidate,
            reason=phone.reason,
            errors=[*errors, *filter(None, [phone.error])],
        )

    repository.save(candidate)
    return IntakeResult(outcome=IntakeOutcome.CREATED, candidate=candidate, errors=errors)


def _parse_payload(
    raw: dict[str, Any],
) -> tuple[ApplicationPayload | None, list[str], list[str]]:
    """Valida el payload. Devuelve (payload, campos opcionales descartados, errores).

    Los textos se recortan y un texto vacío cuenta como ausente. Un campo opcional
    inválido se descarta para no dejar al postulante sin ficha; uno obligatorio rechaza.
    """
    cleaned = {k: v.strip() if isinstance(v, str) else v for k, v in raw.items()}
    cleaned = {k: v for k, v in cleaned.items() if v != ""}
    try:
        return ApplicationPayload.model_validate(cleaned), [], []
    except ValidationError as exc:
        errors = [_format_error(e) for e in exc.errors()]
        bad_fields = {str(e["loc"][0]) for e in exc.errors() if e["loc"]}
    if bad_fields & set(REQUIRED_FIELDS) or not bad_fields:
        return None, [], errors

    for name in bad_fields:
        cleaned.pop(name, None)
    try:
        application = ApplicationPayload.model_validate(cleaned)
    except ValidationError as exc:
        return None, [], errors + [_format_error(e) for e in exc.errors()]
    return application, sorted(bad_fields), errors


def _format_error(error: Any) -> str:
    location = ".".join(str(part) for part in error["loc"]) or "payload"
    return f"{location}: {error['msg']}"
