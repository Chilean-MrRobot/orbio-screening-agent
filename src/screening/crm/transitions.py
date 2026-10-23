"""Cambios de estado que hace el recruiter desde el CRM (spec 013)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, model_validator

from screening.domain import Candidate, CandidateStatus, Stage

RECRUITER_ACTION_EVENT = "recruiter_action"

S = CandidateStatus

# Destino → estados desde los que el recruiter puede mover la ficha.
MANUAL_TRANSITIONS: dict[CandidateStatus, frozenset[CandidateStatus]] = {
    S.INTERVIEW: frozenset(
        {S.QUALIFIED, S.IN_SCREENING, S.CONTACTED, S.NO_RESPONSE, S.INVALID_DATA}
    ),
    S.HIRED: frozenset({S.INTERVIEW}),
    S.DISQUALIFIED: frozenset(set(S) - {S.DISQUALIFIED, S.OPTED_OUT, S.HIRED}),
}


class TransitionError(Exception):
    """El destino no está permitido desde el estado actual."""


class StatusChangeRequest(BaseModel):
    status: Literal["interview", "hired", "disqualified"]
    reason: str | None = None
    recruiter: str | None = None

    @model_validator(mode="after")
    def _reason_for_discard(self) -> Self:
        self.reason = self.reason.strip() if self.reason and self.reason.strip() else None
        if self.status == S.DISQUALIFIED and self.reason is None:
            raise ValueError("descartar exige un motivo")
        return self


def allowed_targets(status: CandidateStatus) -> list[CandidateStatus]:
    return [target for target, sources in MANUAL_TRANSITIONS.items() if status in sources]


def apply_status_change(candidate: Candidate, request: StatusChangeRequest, now: datetime) -> None:
    target = CandidateStatus(request.status)
    if target not in allowed_targets(candidate.status):
        raise TransitionError(f"no se puede pasar de {candidate.status} a {target}")
    candidate.set_status(target, now, reason=request.reason)
    # La conversación automática termina: el caso ya está en manos del recruiter.
    candidate.set_stage(Stage.DONE, now)
    candidate.record_event(
        now,
        RECRUITER_ACTION_EVENT,
        status=target.value,
        reason=request.reason,
        recruiter=request.recruiter,
    )
