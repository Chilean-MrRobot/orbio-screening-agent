"""Resultado del intake. Ver `specs/002-intake.md`."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from screening.domain import Candidate


class IntakeOutcome(StrEnum):
    CREATED = "created"
    DUPLICATE = "duplicate"
    INVALID_DATA = "invalid_data"
    REJECTED = "rejected"


class IntakeReason(StrEnum):
    INVALID_PAYLOAD = "invalid_payload"
    PHONE_MISSING = "phone_missing"
    PHONE_INVALID = "phone_invalid"
    COUNTRY_UNDETERMINED = "country_undetermined"
    COUNTRY_NOT_SERVED = "country_not_served"


class IntakeResult(BaseModel):
    outcome: IntakeOutcome
    candidate: Candidate | None = Field(default=None, description="None solo si es rechazado.")
    reason: IntakeReason | None = None
    errors: list[str] = Field(default_factory=list)
