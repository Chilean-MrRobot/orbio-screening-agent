"""Toques de calentamiento a candidatos en silencio. Ver `specs/011-calentamiento.md`.

Lógica determinista: decide la cadencia de cada ficha, envía el paso que toca según
`cadences.yaml` respetando el horario hábil del país y registra todo en el `timeline`.
La pasada lee solo las fichas vencidas, por índice y paginando (spec 017).
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
from screening.ports import (
    CandidateQuery,
    CandidateRepository,
    MessageChannel,
    OutboundMessage,
    SendResult,
)
from screening.reengagement.hours import is_open, next_opening
from screening.reengagement.models import (
    BackfillReport,
    ReengagementReport,
    TouchOutcome,
    TouchRecord,
)
from screening.reengagement.schedule import (
    ELIGIBLE_STATUSES,
    active_anchor,
    business_hours_for,
    cadence_for,
    first_pending_step,
    schedule_next_touch,
    silence_start,
    step_due_at,
    step_template,
)
from screening.scheduling import awaiting_choice, mark_manual

logger = logging.getLogger(__name__)

# Fuera de esta ventana desde el último mensaje del candidato, Meta solo acepta plantillas.
WHATSAPP_SESSION_WINDOW = timedelta(hours=24)

# Parámetros operativos de la pasada (no de negocio): fichas por consulta y tope por pasada.
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_PER_PASS = 1000


class ReengagementService:
    """`run_due_touches` con sus dependencias, para inyectarlo en la API."""

    def __init__(
        self,
        *,
        repository: CandidateRepository,
        channels: dict[ChannelName, MessageChannel],
        clock: Clock,
        config: AppConfig,
        page_size: int = DEFAULT_PAGE_SIZE,
        max_per_pass: int = DEFAULT_MAX_PER_PASS,
    ) -> None:
        self._repository = repository
        self._channels = channels
        self._clock = clock
        self._config = config
        self._page_size = page_size
        self._max_per_pass = max_per_pass

    def run(self) -> ReengagementReport:
        return run_due_touches(
            repository=self._repository,
            channels=self._channels,
            clock=self._clock,
            config=self._config,
            page_size=self._page_size,
            max_per_pass=self._max_per_pass,
        )


def run_due_touches(
    *,
    repository: CandidateRepository,
    channels: dict[ChannelName, MessageChannel],
    clock: Clock,
    config: AppConfig,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_per_pass: int = DEFAULT_MAX_PER_PASS,
) -> ReengagementReport:
    """Una pasada: envía los toques vencidos. Una ficha que falla no corta la pasada.

    Lee por `next_touch_at` (lo más atrasado primero) hasta `max_per_pass` fichas. Cada ficha
    procesada queda con su próximo toque después de `now` (o sin toque), así que el cursor
    no la vuelve a ver y una pasada repetida o interrumpida no reenvía nada.
    """
    touches = _Pass(repository, channels, clock, config)
    report = touches.report
    now = clock.now()
    cursor: str | None = None
    while True:
        limit = min(page_size, max_per_pass - report.read)
        page = repository.query(
            CandidateQuery(touch_due_before=now, order="next_touch_asc", limit=limit, cursor=cursor)
        )
        for candidate in page.items:
            report.read += 1
            try:
                touches.process(candidate)
            except Exception:
                logger.exception("No se pudo calentar la ficha %s", candidate.id)
                report.errors.append(candidate.id)
        cursor = page.next_cursor
        if cursor is None:
            return report
        if report.read >= max_per_pass:
            report.capped = True
            return report


def backfill_next_touch(
    repository: CandidateRepository, config: AppConfig, *, page_size: int = 200
) -> BackfillReport:
    """Recalcula `next_touch_at` de las fichas abiertas (las anteriores a la spec 017).

    Idempotente: solo guarda las fichas cuyo próximo toque cambia.
    """
    report = BackfillReport()
    cursor: str | None = None
    while True:
        page = repository.query(
            CandidateQuery(statuses=list(ELIGIBLE_STATUSES), limit=page_size, cursor=cursor)
        )
        for candidate in page.items:
            report.read += 1
            due = schedule_next_touch(candidate, config)
            if due != candidate.reengagement.next_touch_at:
                candidate.reengagement.next_touch_at = due
                repository.save(candidate)
                report.updated += 1
        cursor = page.next_cursor
        if cursor is None:
            return report


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
        cadence = cadence_for(candidate, self._config)
        hours = business_hours_for(candidate, self._config)
        if cadence is None or hours is None:
            # Dato viejo: sin cadencia que aplique, sale de la consulta de vencidas.
            if candidate.reengagement.next_touch_at is not None:
                candidate.reengagement.next_touch_at = None
                self._repository.save(candidate)
            return
        self.report.evaluated += 1
        now = self._clock.now()

        anchor = active_anchor(candidate, cadence)
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

    # --- Desde cuándo y cuándo toca ---------------------------------------------------

    def _start(self, candidate: Candidate, cadence: Cadence, now: datetime) -> datetime:
        anchor = silence_start(candidate, cadence)
        index = first_pending_step(candidate, cadence, anchor)
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
        hours = business_hours_for(candidate, self._config)
        assert hours is not None
        state = candidate.reengagement
        state.next_touch_at = step_due_at(
            cadence, state.next_step_index, anchor, hours, self._config.cadences.queue_outside_hours
        )

    # --- Pasos ------------------------------------------------------------------------

    def _touch(
        self,
        candidate: Candidate,
        cadence: Cadence,
        anchor: datetime,
        step: CadenceStep,
        now: datetime,
    ) -> None:
        template = step_template(candidate, step.template, self._config)
        text = render_template(template, candidate, self._config)
        as_template = step.channel == ChannelName.WHATSAPP and self._window_closed(candidate, now)
        result = self._send(candidate, step, template, text, as_template)
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
                template=template,
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
                template=template,
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
            if awaiting_choice(candidate):
                # Spec 021: ya calificó; no se cierra, lo agenda el recruiter a mano.
                mark_manual(candidate, "no_response", now)
            else:
                candidate.set_status(CandidateStatus.NO_RESPONSE, now, reason="reengagement_closed")
            self.report.closed.append(candidate.id)
            return
        state.next_step_index = cadence.steps.index(step) + 1
        self._schedule(candidate, cadence, anchor)

    # --- Envío ------------------------------------------------------------------------

    def _send(
        self, candidate: Candidate, step: CadenceStep, template: str, text: str, as_template: bool
    ) -> SendResult:
        """Nunca lanza: una excepción del canal se devuelve como falla."""
        channel = self._channels.get(step.channel)
        if channel is None or candidate.phone is None:
            return SendResult(ok=False, error=f"canal no configurado: {step.channel}")
        message = OutboundMessage(
            to=candidate.phone,
            text=text,
            template=template if as_template else None,
            template_params=self._template_params(template, candidate) if as_template else {},
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
