"""Cuándo toca el próximo toque de calentamiento. Ver `specs/017-calentamiento-por-indice.md`.

Funciones puras (sin reloj ni repositorio): las usan la pasada y el orquestador para que
`reengagement.next_touch_at`, el campo por el que consulta la pasada, esté siempre al día.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from screening.config import AppConfig
from screening.config.models import BusinessHours, Cadence
from screening.domain import Candidate, CandidateStatus, MessageRole
from screening.reengagement.hours import next_opening

NEVER_RESPONDED = "never_responded"
ABANDONED = "abandoned_mid_screening"

# Únicos estados con conversación pendiente; el resto son finales para el calentamiento.
ELIGIBLE_STATUSES = {CandidateStatus.NEW, CandidateStatus.CONTACTED, CandidateStatus.IN_SCREENING}


def schedule_next_touch(candidate: Candidate, config: AppConfig) -> datetime | None:
    """Cuándo vence el próximo toque, o `None` si no hay cadencia que aplique."""
    cadence = cadence_for(candidate, config)
    hours = business_hours_for(candidate, config)
    if cadence is None or hours is None:
        return None
    anchor = active_anchor(candidate, cadence)
    if anchor is not None:
        index = candidate.reengagement.next_step_index
    else:
        anchor = silence_start(candidate, cadence)
        index = first_pending_step(candidate, cadence, anchor)
    return step_due_at(cadence, index, anchor, hours, config.cadences.queue_outside_hours)


def refresh_next_touch(candidate: Candidate, config: AppConfig) -> None:
    """Deja `next_touch_at` al día; se llama antes de guardar cada vez que la ficha cambia."""
    candidate.reengagement.next_touch_at = schedule_next_touch(candidate, config)


def cadence_for(candidate: Candidate, config: AppConfig) -> Cadence | None:
    if (
        candidate.status not in ELIGIBLE_STATUSES
        or candidate.human_requested
        or candidate.phone is None
    ):
        return None
    conversation = [m for m in candidate.messages if m.role != MessageRole.SYSTEM]
    if candidate.status == CandidateStatus.IN_SCREENING:
        if conversation and conversation[-1].role == MessageRole.AGENT:
            return _cadence(config, ABANDONED)
        return None
    if any(m.role == MessageRole.CANDIDATE for m in conversation):
        return None
    return _cadence(config, NEVER_RESPONDED)


def business_hours_for(candidate: Candidate, config: AppConfig) -> BusinessHours | None:
    if candidate.country is None:
        return None
    return config.cadences.business_hours.get(candidate.country)


def silence_start(candidate: Candidate, cadence: Cadence) -> datetime:
    agent = [m.created_at for m in candidate.messages if m.role == MessageRole.AGENT]
    if cadence.trigger == NEVER_RESPONDED:
        return agent[0] if agent else candidate.created_at
    return candidate.last_agent_message_at or agent[-1]


def active_anchor(candidate: Candidate, cadence: Cadence) -> datetime | None:
    """Inicio del silencio de la cadencia en curso, o `None` si hay que (re)arrancarla."""
    if candidate.reengagement.cadence != cadence.name:
        return None
    started = next(
        (
            e
            for e in reversed(candidate.timeline)
            if e.type == "reengagement_started" and e.detail.get("cadence") == cadence.name
        ),
        None,
    )
    if started is None:
        return None
    anchor = datetime.fromisoformat(started.detail["silence_started_at"])
    replied = candidate.last_candidate_message_at
    if replied is not None and replied > anchor:
        return None
    return anchor


def first_pending_step(candidate: Candidate, cadence: Cadence, anchor: datetime) -> int:
    """Los pasos que ya salieron (el `first_contact` del orquestador) no se repiten."""
    sent = {
        e.detail.get("template")
        for e in candidate.timeline
        if e.type == "message_sent" and e.at >= anchor
    }
    index = 0
    while index < len(cadence.steps) and cadence.steps[index].template in sent:
        index += 1
    return index


def step_due_at(
    cadence: Cadence,
    index: int,
    anchor: datetime,
    hours: BusinessHours,
    queue_outside_hours: bool,
) -> datetime | None:
    """Hora del paso `index`, movida a la próxima apertura si se encola fuera de horario."""
    if index >= len(cadence.steps):
        return None
    nominal = anchor + timedelta(hours=cadence.steps[index].after_hours)
    return next_opening(nominal, hours) if queue_outside_hours else nominal


def _cadence(config: AppConfig, trigger: str) -> Cadence | None:
    return next((c for c in config.cadences.cadences if c.trigger == trigger), None)
