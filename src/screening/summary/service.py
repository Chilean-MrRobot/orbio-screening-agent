"""Resumen post-conversación para el recruiter (spec 012).

Los datos clave salen de la ficha; el LLM solo agrega 2 a 3 observaciones cualitativas.
Si el LLM falla, el resumen queda con los datos clave: nunca bloquea el cierre.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from screening.agent.prompts import PromptTemplate, load_prompt
from screening.config import AppConfig
from screening.conversation.usage import record_usage
from screening.domain import (
    Availability,
    Candidate,
    MessageRole,
    Schedule,
    ScreeningData,
    VehicleType,
)
from screening.ports import ChatMessage, LLMClient, LLMUsage
from screening.summary.models import CandidateSummary, KeyFacts, SummaryNotes

logger = logging.getLogger(__name__)

DEFAULT_PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "summary.md"

SUMMARY_EVENT = "summary_generated"
SUMMARY_ERROR_EVENT = "summary_error"
USAGE_TURN = "summary"
MAX_TRANSCRIPT_MESSAGES = 40

# Eventos del `timeline` que le sirven al LLM para las observaciones.
RELEVANT_EVENTS = (
    "question_received",
    "field_invalid",
    "field_needs_review",
    "human_requested",
    "language_changed",
    "agent_error",
    "message_failed",
)

# Etiquetas legibles para el recruiter; un código desconocido se muestra tal cual.
REASON_LABELS = {
    "no_consent": "no dio su consentimiento para usar sus datos",
    "no_license": "sin licencia de conducir vigente",
    "license_vehicle_mismatch": "su licencia no corresponde al vehículo",
    "out_of_service_area": "ciudad fuera de la zona de servicio",
}
AVAILABILITY_LABELS = {
    Availability.FULL_TIME: "jornada completa",
    Availability.PART_TIME: "media jornada",
    Availability.WEEKENDS: "fines de semana",
}
SCHEDULE_LABELS = {
    Schedule.MORNING: "mañana",
    Schedule.AFTERNOON: "tarde",
    Schedule.EVENING: "noche",
    Schedule.FLEXIBLE: "flexible",
}
VEHICLE_LABELS = {VehicleType.MOTORCYCLE: "moto", VehicleType.CAR: "auto"}
ROLE_LABELS = {MessageRole.CANDIDATE: "candidato", MessageRole.AGENT: "agente"}


def load_summary_prompt(path: Path | str = DEFAULT_PROMPT_PATH) -> PromptTemplate:
    return load_prompt(Path(path))


def build_key_facts(candidate: Candidate, config: AppConfig) -> KeyFacts:
    data = candidate.data
    knockout = candidate.knockout
    reason = knockout.reason_code if knockout and not knockout.passed else None
    return KeyFacts(
        full_name=data.full_name or candidate.application.full_name,
        city=data.city or candidate.application.city,
        country=candidate.country,
        license=_license(data),
        vehicle=_vehicle(data),
        availability=AVAILABILITY_LABELS.get(data.availability) if data.availability else None,
        preferred_schedule=(
            SCHEDULE_LABELS.get(data.preferred_schedule) if data.preferred_schedule else None
        ),
        delivery_experience=_experience(data),
        start_date=data.start_date,
        work_permit=data.work_permit,
        status=candidate.status,
        score=candidate.score.total if candidate.score else None,
        max_score=config.scoring.max_score,
        disqualification_reason=REASON_LABELS.get(reason, reason) if reason else None,
        needs_review=list(data.needs_review),
        human_requested=candidate.human_requested,
    )


class SummaryService:
    """Genera el resumen con `LLMClient.generate_structured` y el modelo de conversación."""

    def __init__(
        self, llm: LLMClient, config: AppConfig, prompt: PromptTemplate | None = None
    ) -> None:
        self._llm = llm
        self._config = config
        self.prompt = prompt or load_summary_prompt()
        self.model = config.llm.conversation_model

    def summarize(self, candidate: Candidate) -> tuple[CandidateSummary, list[LLMUsage]]:
        facts = build_key_facts(candidate, self._config)
        try:
            result = self._llm.generate_structured(
                system=self.prompt.body,
                messages=[ChatMessage("user", _context(candidate, facts))],
                schema=SummaryNotes,
                model=self.model,
            )
        except Exception as exc:
            logger.exception("No se pudo generar el resumen de %s", candidate.id)
            summary = CandidateSummary(
                key_facts=facts,
                source="fallback",
                prompt_version=self.prompt.version,
                error=repr(exc),
            )
            return summary, []
        summary = CandidateSummary(
            key_facts=facts,
            observations=result.output.observations,
            source="llm",
            prompt_version=self.prompt.version,
        )
        return summary, [result.usage]

    def apply(self, candidate: Candidate, at: datetime) -> CandidateSummary:
        """Deja el texto en `candidate.summary` y el detalle y el uso en el `timeline`."""
        summary, usage = self.summarize(candidate)
        record_usage(candidate, at, USAGE_TURN, usage)
        if summary.error:
            candidate.record_event(at, SUMMARY_ERROR_EVENT, error=summary.error)
        candidate.summary = summary.render()
        candidate.record_event(at, SUMMARY_EVENT, **summary.model_dump(mode="json"))
        return summary


# --- Contexto para el LLM ----------------------------------------------------------


def _context(candidate: Candidate, facts: KeyFacts) -> str:
    events = [
        f"- {e.at.isoformat()} {e.type}: {json.dumps(e.detail, ensure_ascii=False, default=str)}"
        for e in candidate.timeline
        if e.type in RELEVANT_EVENTS
    ]
    transcript = [
        f"{ROLE_LABELS.get(m.role, 'sistema')}: {m.text}"
        for m in candidate.messages[-MAX_TRANSCRIPT_MESSAGES:]
    ]
    return "\n\n".join(
        [
            "<datos_clave>\n"
            + json.dumps(facts.model_dump(mode="json"), ensure_ascii=False)
            + "\n</datos_clave>",
            f"<emocion_detectada>{candidate.sentiment}</emocion_detectada>",
            "<eventos>\n" + ("\n".join(events) or "(ninguno)") + "\n</eventos>",
            "<conversacion>\n" + ("\n".join(transcript) or "(vacía)") + "\n</conversacion>",
        ]
    )


# --- Formato de los datos clave ----------------------------------------------------


def _years(value: float) -> str:
    return f"{value:g} {'año' if value == 1 else 'años'}"


def _license(data: ScreeningData) -> str | None:
    if data.has_license is False:
        return "no tiene"
    if data.has_license is None and data.license_type is None:
        return None
    text = data.license_type or "sí"
    if data.license_years is not None:
        text += f" ({_years(data.license_years)})"
    return text


def _vehicle(data: ScreeningData) -> str | None:
    if data.vehicle_type is None:
        return None
    text = VEHICLE_LABELS.get(data.vehicle_type, str(data.vehicle_type))
    if data.owns_vehicle is not None:
        text += ", propia" if data.owns_vehicle else ", no propia"
    return text


def _experience(data: ScreeningData) -> str | None:
    experience = data.delivery_experience
    if experience is None:
        return None
    if experience.years == 0:
        return "sin experiencia en reparto"
    text = _years(experience.years)
    if experience.platforms:
        text += f" ({', '.join(experience.platforms)})"
    return text
