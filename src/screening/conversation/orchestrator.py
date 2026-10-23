"""Orquestador determinista de la conversación. Ver `specs/008-orquestador.md`.

Decide la etapa, aplica lo extraído, corre las reglas y el puntaje y guarda la ficha
después de cada paso. El agente (LLM) solo extrae y redacta.
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from screening.channels import InboundBatch
from screening.clock import Clock
from screening.config import AppConfig
from screening.conversation.messages import render_template
from screening.conversation.stages import CONFIRMATION_FIELD, current_stage, missing_fields
from screening.conversation.usage import record_usage
from screening.domain import (
    ApplicationPayload,
    Candidate,
    CandidateStatus,
    Message,
    MessageRole,
    ScreeningData,
    Stage,
)
from screening.intake import IntakeOutcome, IntakeReason, normalize_phone, process_application
from screening.ports import (
    CandidateRepository,
    ExtractionOutcome,
    MessageChannel,
    OutboundMessage,
    ReplyRequest,
    ScreeningAgent,
    SendResult,
)
from screening.rules import KnockoutEvaluation, SpecialConditionRegistry, evaluate_knockouts
from screening.scoring import compute_score

logger = logging.getLogger(__name__)

# Diseño §6: tras 2 intentos inválidos el campo queda en `needs_review` y se sigue.
MAX_INVALID_ATTEMPTS = 2

# Estados en los que la conversación sigue abierta; los tres primeros pasan a `in_screening`.
RESUMING_STATUSES = {CandidateStatus.NEW, CandidateStatus.CONTACTED, CandidateStatus.NO_RESPONSE}
OPEN_STATUSES = RESUMING_STATUSES | {CandidateStatus.IN_SCREENING}

APOLOGY = "apology"


class Summarizer(Protocol):
    """Resumen post-conversación (spec 012): deja `candidate.summary` y sus eventos."""

    def apply(self, candidate: Candidate, at: datetime) -> object: ...


class ApplicationStartResult(BaseModel):
    outcome: IntakeOutcome
    candidate_id: str | None = None
    status: CandidateStatus | None = None
    reason: IntakeReason | None = None
    errors: list[str] = Field(default_factory=list)
    contacted: bool = False


class ConversationOrchestrator:
    """Implementa `InboundHandler`: procesa un lote de mensajes de un candidato."""

    def __init__(
        self,
        *,
        repository: CandidateRepository,
        agent: ScreeningAgent,
        channel: MessageChannel,
        clock: Clock,
        config: AppConfig,
        registry: SpecialConditionRegistry | None = None,
        summarizer: Summarizer | None = None,
    ) -> None:
        self._repository = repository
        self._agent = agent
        self._channel = channel
        self._clock = clock
        self._config = config
        self._registry = registry
        self._summarizer = summarizer
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # --- Primer contacto ---------------------------------------------------------

    def start_application(self, raw: dict[str, Any]) -> ApplicationStartResult:
        result = process_application(
            raw, repository=self._repository, clock=self._clock, config=self._config
        )
        candidate = result.candidate
        contacted = False
        if result.outcome == IntakeOutcome.CREATED and candidate is not None:
            contacted = self.start(candidate)
        return ApplicationStartResult(
            outcome=result.outcome,
            candidate_id=candidate.id if candidate else None,
            status=candidate.status if candidate else None,
            reason=result.reason,
            errors=result.errors,
            contacted=contacted,
        )

    def start(self, candidate: Candidate) -> bool:
        """Envía `first_contact`; si sale bien, la ficha pasa a `contacted`."""
        text = render_template("first_contact", candidate, self._config)
        ok = self._send(candidate, text, template="first_contact", apologize=False)
        if ok:
            candidate.set_status(CandidateStatus.CONTACTED, self._clock.now())
            self._repository.save(candidate)
        return ok

    # --- Mensajes entrantes ------------------------------------------------------

    def handle(self, batch: InboundBatch) -> None:
        with self._lock_for(batch.sender):
            try:
                self._handle(batch)
            except Exception:
                logger.exception("No se pudo procesar el lote de %s", batch.sender)

    def _handle(self, batch: InboundBatch) -> None:
        now = self._clock.now()
        candidate = self._repository.get_by_phone(batch.sender) or self._new_candidate(batch, now)
        known = {m.external_id for m in candidate.messages if m.external_id}
        new = [m.to_domain_message() for m in batch.messages if m.external_id not in known]
        if not new:
            return

        # Persistir primero: aunque falle lo que sigue, el mensaje queda en la ficha.
        candidate.messages.extend(new)
        candidate.last_candidate_message_at = now
        candidate.record_event(
            now, "message_received", count=len(new), external_ids=[m.external_id for m in new]
        )
        self._repository.save(candidate)

        try:
            self._converse(candidate, new)
        except Exception as exc:
            logger.exception("Error del orquestador con %s", candidate.id)
            candidate.record_event(self._clock.now(), "orchestrator_error", error=repr(exc))
            self._repository.save(candidate)
            self._apologize(candidate)

    def _converse(self, candidate: Candidate, new: list[Message]) -> None:
        if candidate.status == CandidateStatus.OPTED_OUT:
            return
        if candidate.status not in OPEN_STATUSES:
            candidate.record_event(self._clock.now(), "message_after_close")
            self._repository.save(candidate)
            return
        if candidate.status in RESUMING_STATUSES:
            candidate.set_status(CandidateStatus.IN_SCREENING, self._clock.now())

        evaluation = self._evaluate(candidate)
        stage = current_stage(candidate, evaluation, self._config)
        in_closing = candidate.stage == Stage.CLOSING and stage == Stage.CLOSING
        expected = (
            [CONFIRMATION_FIELD]
            if in_closing
            else missing_fields(candidate, stage, evaluation, self._config)
        )

        try:
            outcome = self._agent.extract(candidate, new, expected)
        except Exception as exc:
            self._agent_failed(candidate, "extract", exc)
            return

        now = self._clock.now()
        record_usage(candidate, now, "extract", outcome.usage)
        if outcome.language != candidate.language:
            candidate.record_event(
                now, "language_changed", previous=candidate.language, new=outcome.language
            )
            candidate.language = outcome.language
        candidate.sentiment = outcome.sentiment

        if outcome.opt_out:
            candidate.set_status(CandidateStatus.OPTED_OUT, now, reason="opt_out")
            self._repository.save(candidate)
            return

        changed = self._apply_updates(candidate, outcome, now)
        retry = self._register_invalid(candidate, outcome, changed, now)
        if changed:
            evaluation = self._evaluate(candidate)
            self._record_evaluation(candidate, evaluation, now)
        if evaluation is not None:
            candidate.knockout = evaluation.result
        self._repository.save(candidate)

        if evaluation is not None and evaluation.result and not evaluation.result.passed:
            self._disqualify(candidate, evaluation)
            return

        stage = current_stage(candidate, evaluation, self._config)
        confirmed = (
            in_closing
            and stage == Stage.CLOSING
            and CONFIRMATION_FIELD in outcome.provided_fields
            and not changed
        )
        candidate.set_stage(stage, now)

        if outcome.human_requested:
            candidate.human_requested = True
            candidate.record_event(now, "human_requested")
            self._send(
                candidate,
                render_template("human_handoff", candidate, self._config),
                template="human_handoff",
            )
            return
        if confirmed:
            self._qualify(candidate)
            return
        if outcome.question:
            candidate.record_event(now, "question_received", question=outcome.question)
        self._reply(candidate, stage, evaluation, retry, outcome.question)

    # --- Pasos -------------------------------------------------------------------

    def _apply_updates(
        self, candidate: Candidate, outcome: ExtractionOutcome, now: datetime
    ) -> list[str]:
        """Sobrescribe la ficha con cada campo no vacío de `updates` (las correcciones ganan)."""
        data = candidate.data
        before = data.model_dump(mode="json")
        after = outcome.updates.model_dump(mode="json")
        changed: list[str] = []
        for name in ScreeningData.model_fields:
            value = getattr(outcome.updates, name)
            if name == "needs_review" or value is None:
                continue
            if name in data.needs_review:
                data.needs_review.remove(name)
            if getattr(data, name) == value:
                continue
            setattr(data, name, value)
            candidate.record_event(
                now, "field_updated", field=name, value=after[name], previous=before[name]
            )
            changed.append(name)
        return changed

    def _register_invalid(
        self, candidate: Candidate, outcome: ExtractionOutcome, changed: list[str], now: datetime
    ) -> list[str]:
        """Cuenta los intentos inválidos en el `timeline`; devuelve los campos a repreguntar."""
        data = candidate.data
        retry: list[str] = []
        for name in dict.fromkeys(outcome.invalid_fields):
            if (
                name not in ScreeningData.model_fields
                or name == "needs_review"
                or name in changed
                or name in data.needs_review
                or getattr(outcome.updates, name) is not None
            ):
                continue
            previous = sum(
                1
                for e in candidate.timeline
                if e.type == "field_invalid" and e.detail.get("field") == name
            )
            attempt = previous + 1
            candidate.record_event(now, "field_invalid", field=name, attempt=attempt)
            if attempt >= MAX_INVALID_ATTEMPTS:
                data.needs_review.append(name)
                candidate.record_event(now, "field_needs_review", field=name)
            else:
                retry.append(name)
        return retry

    def _evaluate(self, candidate: Candidate) -> KnockoutEvaluation | None:
        if candidate.country is None or candidate.country not in self._config.regions:
            return None
        return evaluate_knockouts(candidate.data, candidate.country, self._config, self._registry)

    def _record_evaluation(
        self, candidate: Candidate, evaluation: KnockoutEvaluation | None, now: datetime
    ) -> None:
        if evaluation is None:
            candidate.record_event(now, "knockouts_skipped", reason="country_not_configured")
            return
        result = evaluation.result
        candidate.record_event(
            now,
            "knockouts_evaluated",
            passed=evaluation.passed_rules,
            pending=[r.name for r in evaluation.pending_rules],
            failed_rule=result.failed_rule if result else None,
            reason_code=result.reason_code if result else None,
        )

    def _disqualify(self, candidate: Candidate, evaluation: KnockoutEvaluation) -> None:
        now = self._clock.now()
        result = evaluation.result
        assert result is not None and evaluation.message_template is not None
        candidate.set_stage(Stage.DONE, now)
        candidate.set_status(CandidateStatus.DISQUALIFIED, now, reason=result.reason_code)
        self._repository.save(candidate)
        template = evaluation.message_template
        self._send(candidate, render_template(template, candidate, self._config), template)
        self._summarize(candidate)

    def _qualify(self, candidate: Candidate) -> None:
        now = self._clock.now()
        candidate.score = compute_score(
            candidate.data, self._config.scoring, self._today(candidate)
        )
        candidate.record_event(
            now,
            "score_computed",
            total=candidate.score.total,
            components=candidate.score.components,
        )
        candidate.set_stage(Stage.DONE, now)
        candidate.set_status(CandidateStatus.QUALIFIED, now)
        self._repository.save(candidate)
        template = "qualified_next_steps"
        self._send(candidate, render_template(template, candidate, self._config), template)
        self._summarize(candidate)

    def _summarize(self, candidate: Candidate) -> None:
        """Después del mensaje final, para que el candidato no espere; nunca bloquea el cierre."""
        if self._summarizer is None:
            return
        now = self._clock.now()
        try:
            self._summarizer.apply(candidate, now)
        except Exception as exc:
            logger.exception("No se pudo resumir la ficha %s", candidate.id)
            candidate.record_event(now, "summary_error", error=repr(exc))
        self._repository.save(candidate)

    def _reply(
        self,
        candidate: Candidate,
        stage: Stage,
        evaluation: KnockoutEvaluation | None,
        retry: list[str],
        question: str | None,
    ) -> None:
        closing = stage == Stage.CLOSING
        missing = [] if closing else missing_fields(candidate, stage, evaluation, self._config)
        request = ReplyRequest(
            candidate=candidate,
            stage=stage,
            ask_fields=[f for f in missing if f not in retry],
            retry_fields=retry,
            question=question,
            closing_summary=closing,
        )
        try:
            reply = self._agent.reply(request)
        except Exception as exc:
            self._agent_failed(candidate, "reply", exc)
            return
        record_usage(candidate, self._clock.now(), "reply", reply.usage)
        self._send(candidate, reply.text)

    # --- Envío y errores ---------------------------------------------------------

    def _send(
        self, candidate: Candidate, text: str, template: str | None = None, apologize: bool = True
    ) -> bool:
        """Envía, registra el resultado y guarda. Nunca lanza por el canal."""
        now = self._clock.now()
        if candidate.phone is None:
            result = SendResult(ok=False, error="la ficha no tiene teléfono")
        else:
            try:
                result = self._channel.send(OutboundMessage(to=candidate.phone, text=text))
            except Exception as exc:
                logger.exception("El canal falló enviando a %s", candidate.id)
                result = SendResult(ok=False, error=repr(exc))

        if result.ok:
            candidate.messages.append(
                Message(
                    role=MessageRole.AGENT,
                    text=text,
                    channel=self._channel.name,
                    created_at=now,
                    external_id=result.external_id,
                )
            )
            candidate.last_agent_message_at = now
            candidate.record_event(
                now, "message_sent", template=template, external_id=result.external_id
            )
        else:
            logger.warning("Envío a %s falló: %s", candidate.id, result.error)
            candidate.record_event(now, "message_failed", template=template, error=result.error)
        self._repository.save(candidate)

        if not result.ok and apologize and template != APOLOGY:
            self._apologize(candidate)
        return result.ok

    def _apologize(self, candidate: Candidate) -> None:
        self._send(candidate, render_template(APOLOGY, candidate, self._config), APOLOGY)

    def _agent_failed(self, candidate: Candidate, turn: str, exc: Exception) -> None:
        logger.exception("El agente falló en el turno %s con %s", turn, candidate.id)
        candidate.record_event(self._clock.now(), "agent_error", turn=turn, error=repr(exc))
        self._repository.save(candidate)
        self._apologize(candidate)

    # --- Utilidades --------------------------------------------------------------

    def _new_candidate(self, batch: InboundBatch, now: datetime) -> Candidate:
        """Ficha mínima para quien escribe sin haber postulado por el portal."""
        country = normalize_phone(batch.sender, None, self._config.regions).country
        profile_name = next((m.profile_name for m in batch.messages if m.profile_name), None)
        application = ApplicationPayload(
            application_id=f"inbound-{batch.messages[0].external_id}",
            source="whatsapp",
            full_name=profile_name,
            phone=batch.sender,
            country=country,
        )
        candidate = Candidate(
            application=application,
            phone=batch.sender,
            country=country,
            created_at=now,
            updated_at=now,
        )
        candidate.record_event(now, "candidate_created", source="whatsapp")
        return candidate

    def _today(self, candidate: Candidate) -> date:
        """Fecha local de la región del candidato, a partir del `Clock`."""
        now = self._clock.now()
        region = self._config.regions.get(candidate.country) if candidate.country else None
        return now.astimezone(ZoneInfo(region.timezone)).date() if region else now.date()

    def _lock_for(self, sender: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(sender, threading.Lock())
