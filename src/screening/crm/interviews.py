"""El recruiter agenda, reagenda o cancela la entrevista desde el panel (spec 021)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal, Self

from pydantic import BaseModel, model_validator

from screening.crm.transitions import TransitionError
from screening.domain import Candidate, CandidateStatus, InterviewSlot, Language, Stage
from screening.scheduling import InterviewScheduler, format_slot

# Estados desde los que el recruiter puede agendar o reagendar.
BOOKABLE_STATUSES = frozenset({CandidateStatus.QUALIFIED, CandidateStatus.INTERVIEW})
DEFAULT_BOOKER = "recruiter"


class SlotUnavailableError(Exception):
    """El horario está ocupado o ya no es válido (pasó la antelación o está fuera de horario)."""


class SlotOption(BaseModel):
    """Horario libre para el selector del panel, con la etiqueta en hora local."""

    start: datetime
    end: datetime
    label: str
    timezone: str


def slot_options(
    candidate: Candidate, scheduler: InterviewScheduler, limit: int
) -> list[SlotOption]:
    if candidate.country is None or not scheduler.enabled_for(candidate):
        return []
    timezone = scheduler.timezone(candidate.country)
    return [
        SlotOption(
            start=s.start,
            end=s.end,
            label=format_slot(s, timezone, Language.ES),
            timezone=timezone,
        )
        for s in scheduler.free_slots(candidate.country, limit=limit)
    ]


class InterviewChangeRequest(BaseModel):
    action: Literal["book", "cancel"]
    start: datetime | None = None
    recruiter: str | None = None

    @model_validator(mode="after")
    def _start_for_booking(self) -> Self:
        if self.action == "book" and self.start is None:
            raise ValueError("agendar exige el inicio del horario")
        self.recruiter = (self.recruiter or "").strip() or None
        return self


def apply_interview_change(
    candidate: Candidate,
    request: InterviewChangeRequest,
    scheduler: InterviewScheduler,
    now: datetime,
) -> None:
    if request.action == "book":
        assert request.start is not None
        _book(candidate, request.start, request.recruiter, scheduler, now)
    else:
        _cancel(candidate, request.recruiter, scheduler, now)


def _book(
    candidate: Candidate,
    start: datetime,
    recruiter: str | None,
    scheduler: InterviewScheduler,
    now: datetime,
) -> None:
    if candidate.status not in BOOKABLE_STATUSES:
        raise TransitionError(f"no se puede agendar una entrevista en estado {candidate.status}")
    settings = scheduler.settings
    if settings is None or not scheduler.enabled_for(candidate):
        raise SlotUnavailableError("el agendamiento no está activo para el país del candidato")
    previous = candidate.interview
    slot = InterviewSlot(start=start, end=start + timedelta(minutes=settings.slot_minutes))
    interview = scheduler.book(candidate, slot, booked_by=recruiter or DEFAULT_BOOKER)
    if interview is None:
        raise SlotUnavailableError("el horario ya no está disponible")

    state = candidate.scheduling
    state.offered = []
    state.manual_required = False
    state.manual_reason = None
    if previous is not None and previous.slot_id != interview.slot_id:
        candidate.record_event(
            now,
            "interview_rescheduled",
            start=interview.start.isoformat(),
            previous_start=previous.start.isoformat(),
            recruiter=recruiter,
        )
    elif previous is None:
        candidate.record_event(
            now,
            "interview_booked",
            start=interview.start.isoformat(),
            slot_id=interview.slot_id,
            booked_by=interview.booked_by,
        )
    candidate.set_stage(Stage.DONE, now)
    candidate.set_status(CandidateStatus.INTERVIEW, now, reason="interview_booked")


def _cancel(
    candidate: Candidate, recruiter: str | None, scheduler: InterviewScheduler, now: datetime
) -> None:
    if candidate.interview is None or candidate.status != CandidateStatus.INTERVIEW:
        raise TransitionError("la ficha no tiene una entrevista agendada")
    cancelled = scheduler.cancel(candidate)
    assert cancelled is not None
    candidate.scheduling.manual_required = True
    candidate.scheduling.manual_reason = "cancelled"
    candidate.record_event(
        now, "interview_cancelled", start=cancelled.start.isoformat(), recruiter=recruiter
    )
    candidate.set_status(CandidateStatus.QUALIFIED, now, reason="interview_cancelled")
