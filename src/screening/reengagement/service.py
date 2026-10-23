"""Toques de calentamiento a candidatos en silencio. Ver `specs/011-calentamiento.md`.

Lógica determinista: decide la cadencia de cada ficha, envía el paso que toca según
`cadences.yaml` respetando el horario hábil del país y registra todo en el `timeline`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from string import Formatter

from screening.clock import Clock
from screening.config import AppConfig
from screening.config.models import Cadence, CadenceStep
from screening.conversation.messages import DEFAULT_TEMPLATES, render_template, template_variables
from screening.domain import (
    Candidate,
    CandidateStatus,
    ChannelName,
    Language,
    Message,
    MessageRole,
    ReengagementState,
)
from screening.ports import CandidateRepository, MessageChannel, OutboundMessage, SendResult
from screening.reengagement.hours import is_open, next_opening
from screening.reengagement.models import ReengagementReport, TouchOutcome, TouchRecord

logger = logging.getLogger(__name__)

# Fuera de esta ventana desde el último mensaje del candidato, Meta solo acepta plantillas.
WHATSAPP_SESSION_WINDOW = timedelta(hours=24)

NEVER_RESPONDED = "never_responded"
ABANDONED = "abandoned_mid_screening"

# Únicos estados con conversación pendiente; el resto son finales para el calentamiento.
ELIGIBLE_STATUSES = {CandidateStatus.NEW, CandidateStatus.CONTACTED, CandidateStatus.IN_SCREENING}


class ReengagementService:
    """`run_due_touches` con sus dependencias, para inyectarlo en la API."""

    def __init__(
        self,
        *,
        repository: CandidateRepository,
        channels: dict[ChannelName, MessageChannel],
        clock: Clock,
        config: AppConfig,
    ) -> None:
        self._repository = repository
        self._channels = channels
        self._clock = clock
        self._config = config

    def run(self) -> ReengagementReport:
        return run_due_touches(
            repository=self._repository,
            channels=self._channels,
            clock=self._clock,
            config=self._config,
        )


def run_due_touches(
    *,
    repository: CandidateRepository,
    channels: dict[ChannelName, MessageChannel],
    clock: Clock,
    config: AppConfig,
) -> ReengagementReport:
    """Una pasada: envía los toques vencidos. Una ficha que falla no corta la pasada."""
    touches = _Pass(repository, channels, clock, config)
    for candidate in repository.list():
        try:
            touches.process(candidate)
        except Exception:
            logger.exception("No se pudo calentar la ficha %s", candidate.id)
            touches.report.errors.append(candidate.id)
    return touches.report


class _Pass:
    def __init__(
        self,
        repository: CandidateRepository,
        channels: dict[ChannelName, MessageChannel],
        clock: Clock,
        config: AppConfig,
    ) -> None:
        self._repository = repository
        self._channels = channels
        self._clock = clock
        self._config = config
        self.report = ReengagementReport()

    def process(self, candidate: Candidate) -> None:
        cadence = self._cadence_for(candidate)
        hours = self._config.cadences.business_hours.get(candidate.country)  # type: ignore[arg-type]
        if cadence is None or hours is None:
            return
        self.report.evaluated += 1
        now = self._clock.now()

        anchor = self._active_anchor(candidate, cadence)
        if anchor is None:
            anchor = self._start(candidate, cadence, now)
        state = candidate.reengagement
        if state.next_touch_at is None or now < state.next_touch_at:
            return

        steps = cadence.steps
        due = [
            i
            for i in range(state.next_step_index, len(steps))
            if anchor + timedelta(hours=steps[i].after_hours) <= now
        ]
        if not due:
            self._schedule(candidate, cadence, anchor)
            self._repository.save(candidate)
            return

        # Pasos atrasados: solo se envía el último vencido; nunca una ráfaga.
        for index in due[:-1]:
            if self._skip(candidate, cadence, anchor, steps[index], "superseded", now):
                self._repository.save(candidate)
                return

        step = steps[due[-1]]
        if not is_open(now, hours):
            if self._config.cadences.queue_outside_hours:
                state.next_touch_at = next_opening(now, hours)
                candidate.record_event(
                    now,
                    "reengagement_touch_queued",
                    cadence=cadence.name,
                    step=step.name,
                    until=state.next_touch_at.isoformat(),
                )
                self._record(candidate, cadence, step, TouchOutcome.QUEUED)
            else:
                self._skip(candidate, cadence, anchor, step, "outside_business_hours", now)
            self._repository.save(candidate)
            return

        self._touch(candidate, cadence, anchor, step, now)
        self._repository.save(candidate)

    # --- Qué cadencia aplica y desde cuándo -------------------------------------------

    def _cadence_for(self, candidate: Candidate) -> Cadence | None:
        if (
            candidate.status not in ELIGIBLE_STATUSES
            or candidate.human_requested
            or candidate.phone is None
        ):
            return None
        conversation = [m for m in candidate.messages if m.role != MessageRole.SYSTEM]
        if candidate.status == CandidateStatus.IN_SCREENING:
            if conversation and conversation[-1].role == MessageRole.AGENT:
                return self._cadence(ABANDONED)
            return None
        if any(m.role == MessageRole.CANDIDATE for m in conversation):
            return None
        return self._cadence(NEVER_RESPONDED)

    def _cadence(self, trigger: str) -> Cadence | None:
        return next((c for c in self._config.cadences.cadences if c.trigger == trigger), None)

    def _silence_start(self, candidate: Candidate, cadence: Cadence) -> datetime:
        agent = [m.created_at for m in candidate.messages if m.role == MessageRole.AGENT]
        if cadence.trigger == NEVER_RESPONDED:
            return agent[0] if agent else candidate.created_at
        return candidate.last_agent_message_at or agent[-1]

    def _active_anchor(self, candidate: Candidate, cadence: Cadence) -> datetime | None:
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

    def _start(self, candidate: Candidate, cadence: Cadence, now: datetime) -> datetime:
        anchor = self._silence_start(candidate, cadence)
        # Los pasos que ya salieron (el `first_contact` del orquestador) no se repiten.
        sent = {
            e.detail.get("template")
            for e in candidate.timeline
            if e.type == "message_sent" and e.at >= anchor
        }
        index = 0
        while index < len(cadence.steps) and cadence.steps[index].template in sent:
            index += 1
        candidate.reengagement = ReengagementState(cadence=cadence.name, next_step_index=index)
        candidate.record_event(
            now,
            "reengagement_started",
            cadence=cadence.name,
            silence_started_at=anchor.isoformat(),
            stage=candidate.stage,
        )
        self._schedule(candidate, cadence, anchor)
        self._repository.save(candidate)
        return anchor

    def _schedule(self, candidate: Candidate, cadence: Cadence, anchor: datetime) -> None:
        state = candidate.reengagement
        if state.next_step_index >= len(cadence.steps):
            state.next_touch_at = None
            return
        step = cadence.steps[state.next_step_index]
        state.next_touch_at = anchor + timedelta(hours=step.after_hours)

    # --- Pasos ------------------------------------------------------------------------

    def _touch(
        self,
        candidate: Candidate,
        cadence: Cadence,
        anchor: datetime,
        step: CadenceStep,
        now: datetime,
    ) -> None:
        text = render_template(step.template, candidate, self._config)
        as_template = step.channel == ChannelName.WHATSAPP and self._window_closed(candidate, now)
        result = self._send(candidate, step, text, as_template)
        state = candidate.reengagement

        if result.ok:
            candidate.messages.append(
                Message(
                    role=MessageRole.AGENT,
                    text=text,
                    channel=step.channel,
                    created_at=now,
                    external_id=result.external_id,
                )
            )
            candidate.last_agent_message_at = now
            candidate.record_event(
                now,
                "reengagement_touch_sent",
                cadence=cadence.name,
                step=step.name,
                channel=step.channel,
                template=step.template,
                external_id=result.external_id,
                as_template=as_template,
            )
            self._record(candidate, cadence, step, TouchOutcome.SENT)
            state.touches_sent += 1
            if candidate.status == CandidateStatus.NEW:
                candidate.set_status(CandidateStatus.CONTACTED, now)
        else:
            logger.warning("Toque %s a %s falló: %s", step.name, candidate.id, result.error)
            candidate.record_event(
                now,
                "reengagement_touch_failed",
                cadence=cadence.name,
                step=step.name,
                channel=step.channel,
                template=step.template,
                error=result.error,
            )
            self._record(candidate, cadence, step, TouchOutcome.FAILED, result.error)
            if not step.closes:
                return  # queda pendiente: se reintenta en la próxima pasada

        self._advance(candidate, cadence, anchor, step, now)

    def _skip(
        self,
        candidate: Candidate,
        cadence: Cadence,
        anchor: datetime,
        step: CadenceStep,
        reason: str,
        now: datetime,
    ) -> bool:
        """Salta el paso sin enviarlo. Devuelve si con eso la ficha quedó cerrada."""
        candidate.record_event(
            now, "reengagement_touch_skipped", cadence=cadence.name, step=step.name, reason=reason
        )
        self._record(candidate, cadence, step, TouchOutcome.SKIPPED, reason)
        self._advance(candidate, cadence, anchor, step, now)
        return step.closes

    def _advance(
        self,
        candidate: Candidate,
        cadence: Cadence,
        anchor: datetime,
        step: CadenceStep,
        now: datetime,
    ) -> None:
        state = candidate.reengagement
        if step.closes:
            state.next_step_index = len(cadence.steps)
            state.next_touch_at = None
            candidate.set_status(CandidateStatus.NO_RESPONSE, now, reason="reengagement_closed")
            self.report.closed.append(candidate.id)
            return
        state.next_step_index = cadence.steps.index(step) + 1
        self._schedule(candidate, cadence, anchor)

    # --- Envío ------------------------------------------------------------------------

    def _send(
        self, candidate: Candidate, step: CadenceStep, text: str, as_template: bool
    ) -> SendResult:
        """Nunca lanza: una excepción del canal se devuelve como falla."""
        channel = self._channels.get(step.channel)
        if channel is None or candidate.phone is None:
            return SendResult(ok=False, error=f"canal no configurado: {step.channel}")
        message = OutboundMessage(
            to=candidate.phone,
            text=text,
            template=step.template if as_template else None,
            template_params=self._template_params(step.template, candidate) if as_template else {},
        )
        try:
            return channel.send(message)
        except Exception as exc:
            logger.exception("El canal %s falló enviando a %s", step.channel, candidate.id)
            return SendResult(ok=False, error=repr(exc))

    def _window_closed(self, candidate: Candidate, now: datetime) -> bool:
        last = candidate.last_candidate_message_at
        return last is None or now - last >= WHATSAPP_SESSION_WINDOW

    def _template_params(self, key: str, candidate: Candidate) -> dict[str, str]:
        """Variables con nombre que usa la plantilla, para la plantilla aprobada en Meta."""
        texts = self._config.templates.get(key) or DEFAULT_TEMPLATES[key]
        raw = texts.get(candidate.language) or texts[Language.ES]
        variables = template_variables(candidate, self._config)
        names = [name for _, name, _, _ in Formatter().parse(raw) if name]
        return {name: variables[name] for name in dict.fromkeys(names)}

    def _record(
        self,
        candidate: Candidate,
        cadence: Cadence,
        step: CadenceStep,
        outcome: TouchOutcome,
        error: str | None = None,
    ) -> None:
        self.report.touches.append(
            TouchRecord(
                candidate_id=candidate.id,
                cadence=cadence.name,
                step=step.name,
                channel=step.channel,
                outcome=outcome,
                error=error if outcome == TouchOutcome.FAILED else None,
            )
        )
