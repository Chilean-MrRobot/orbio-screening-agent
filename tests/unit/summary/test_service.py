from __future__ import annotations

import json
from datetime import date

import pytest

from screening.adapters.fake_llm import FakeLLMClient, ScriptedResponse
from screening.clock import FixedClock
from screening.config import AppConfig
from screening.domain import (
    Availability,
    Candidate,
    CandidateStatus,
    ChannelName,
    DeliveryExperience,
    KnockoutResult,
    Message,
    MessageRole,
    Schedule,
    ScoreBreakdown,
    ScreeningData,
    VehicleType,
)
from screening.ports import LLMError, LLMUsage
from screening.summary import (
    DEFAULT_PROMPT_PATH,
    SummaryNotes,
    SummaryService,
    build_key_facts,
    load_summary_prompt,
)

NOTES = SummaryNotes(observations=["Preguntó cómo se paga.", "Tono cordial y respuestas rápidas."])


def qualified(candidate: Candidate, clock: FixedClock) -> Candidate:
    candidate.data = ScreeningData(
        full_name="Lucía Pérez",
        consent=True,
        has_license=True,
        license_type="B",
        license_years=3,
        vehicle_type=VehicleType.MOTORCYCLE,
        owns_vehicle=True,
        city="Madrid",
        availability=Availability.FULL_TIME,
        preferred_schedule=Schedule.AFTERNOON,
        delivery_experience=DeliveryExperience(years=2, platforms=["Glovo"]),
        start_date=date(2026, 10, 26),
        work_permit=True,
    )
    candidate.knockout = KnockoutResult(passed=True)
    candidate.score = ScoreBreakdown(total=82, components={"experience": 30})
    candidate.status = CandidateStatus.QUALIFIED
    candidate.messages = [
        Message(role=MessageRole.AGENT, text="¿Tienes carnet?", channel=ChannelName.WHATSAPP),
        Message(role=MessageRole.CANDIDATE, text="sí, el B", channel=ChannelName.WHATSAPP),
    ]
    candidate.record_event(clock.now(), "question_received", question="¿Cómo se paga?")
    return candidate


def disqualified(candidate: Candidate) -> Candidate:
    candidate.data = ScreeningData(consent=True, has_license=False)
    candidate.knockout = KnockoutResult(
        passed=False, failed_rule="has_license", reason_code="no_license"
    )
    candidate.status = CandidateStatus.DISQUALIFIED
    return candidate


def service(app_config: AppConfig, *responses: object) -> tuple[SummaryService, FakeLLMClient]:
    llm = FakeLLMClient(responses)
    return SummaryService(llm, app_config), llm


# --- Datos clave -------------------------------------------------------------------


def test_key_facts_come_from_the_record(candidate, clock, app_config) -> None:
    facts = build_key_facts(qualified(candidate, clock), app_config)

    assert facts.full_name == "Lucía Pérez"
    assert facts.license == "B (3 años)"
    assert facts.vehicle == "moto, propia"
    assert facts.availability == "jornada completa"
    assert facts.preferred_schedule == "tarde"
    assert facts.delivery_experience == "2 años (Glovo)"
    assert facts.score == 82
    assert facts.max_score == app_config.scoring.max_score
    assert facts.disqualification_reason is None


def test_missing_experience_is_not_the_same_as_none(candidate, app_config) -> None:
    assert build_key_facts(candidate, app_config).delivery_experience is None

    candidate.data.delivery_experience = DeliveryExperience(years=0)
    assert build_key_facts(candidate, app_config).delivery_experience == (
        "sin experiencia en reparto"
    )


def test_name_and_city_fall_back_to_the_application(candidate, app_config) -> None:
    facts = build_key_facts(candidate, app_config)

    assert (facts.full_name, facts.city) == ("Lucía Pérez", "Madrid")


def test_unknown_reason_code_is_shown_as_is(candidate, app_config) -> None:
    disqualified(candidate).knockout.reason_code = "new_rule"

    assert build_key_facts(candidate, app_config).disqualification_reason == "new_rule"


# --- Resumen con LLM ---------------------------------------------------------------


def test_qualified_summary_has_facts_score_and_observations(candidate, clock, app_config) -> None:
    summarizer, llm = service(app_config, NOTES)

    summary, usage = summarizer.summarize(qualified(candidate, clock))
    text = summary.render()

    assert summary.source == "llm"
    assert text.startswith("Lucía Pérez · Madrid (ES) · Calificado · Puntaje 82/100")
    assert "Licencia: B (3 años) · Vehículo: moto, propia" in text
    assert "Experiencia: 2 años (Glovo) · Inicio: 2026-10-26 · Permiso de trabajo: sí" in text
    assert text.endswith(
        "Observaciones:\n- Preguntó cómo se paga.\n- Tono cordial y respuestas rápidas."
    )
    assert "Revisar" not in text and "persona" not in text
    assert len(usage) == 1

    [call] = llm.calls
    assert call.method == "structured"
    assert call.schema is SummaryNotes
    assert call.model == app_config.llm.conversation_model
    assert call.system == load_summary_prompt().body
    context = call.messages[0].content
    assert "¿Cómo se paga?" in context  # eventos del timeline
    assert "candidato: sí, el B" in context  # transcripción
    assert '"score": 82.0' in context  # datos clave


def test_disqualified_summary_shows_the_reason_without_score(candidate, app_config) -> None:
    summarizer, _ = service(app_config, NOTES)

    text = summarizer.summarize(disqualified(candidate))[0].render()

    assert "Descartado · Motivo: sin licencia de conducir vigente" in text
    assert "Puntaje" not in text
    assert "Licencia: no tiene" in text


def test_needs_review_is_listed_and_sent_to_the_llm(candidate, clock, app_config) -> None:
    qualified(candidate, clock).data.needs_review = ["start_date"]
    candidate.data.start_date = None
    summarizer, llm = service(app_config, NOTES)

    text = summarizer.summarize(candidate)[0].render()

    assert "Revisar: start_date" in text
    assert "Inicio: no informado" in text
    assert '"needs_review": ["start_date"]' in llm.calls[0].messages[0].content


def test_human_request_is_shown(candidate, clock, app_config) -> None:
    qualified(candidate, clock).human_requested = True
    summarizer, _ = service(app_config, NOTES)

    assert "Pidió hablar con una persona." in summarizer.summarize(candidate)[0].render()


def test_transcript_is_capped(candidate, clock, app_config) -> None:
    qualified(candidate, clock).messages = [
        Message(role=MessageRole.CANDIDATE, text=f"mensaje {i}", channel=ChannelName.WHATSAPP)
        for i in range(60)
    ]
    summarizer, llm = service(app_config, NOTES)

    summarizer.summarize(candidate)

    context = llm.calls[0].messages[0].content
    assert "mensaje 59" in context and "mensaje 19" not in context


# --- Respaldo ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "response", [LLMError("timeout"), {"observations": []}], ids=["error", "invalid"]
)
def test_llm_failure_falls_back_to_key_facts(candidate, clock, app_config, response) -> None:
    summarizer, _ = service(app_config, response)

    summary, usage = summarizer.summarize(qualified(candidate, clock))

    assert summary.source == "fallback"
    assert summary.observations == []
    assert "LLMError" in (summary.error or "")
    assert usage == []
    assert summary.render().startswith("Lucía Pérez · Madrid (ES) · Calificado")
    assert summary.render().endswith(
        "Observaciones: no disponibles (no se pudo generar el resumen automático)."
    )


# --- apply -------------------------------------------------------------------------


def test_apply_stores_text_event_and_usage(candidate, clock, app_config) -> None:
    usage = LLMUsage("gpt-5-mini", 300, 40, 900.0, 0.0004)
    summarizer, _ = service(app_config, ScriptedResponse(NOTES, usage))

    summary = summarizer.apply(qualified(candidate, clock), clock.now())

    assert candidate.summary == summary.render()
    [event] = [e for e in candidate.timeline if e.type == "summary_generated"]
    assert event.at == clock.now()
    assert event.detail["source"] == "llm"
    assert event.detail["key_facts"]["license"] == "B (3 años)"
    assert event.detail["observations"] == NOTES.observations
    assert event.detail["prompt_version"].startswith("v1-")
    json.dumps(event.detail)  # serializable para Firestore
    [usage_event] = [e for e in candidate.timeline if e.type == "llm_usage"]
    assert usage_event.detail["turn"] == "summary"
    assert usage_event.detail["input_tokens"] == 300


def test_apply_records_the_error_on_fallback(candidate, clock, app_config) -> None:
    summarizer, _ = service(app_config, LLMError("boom"))

    summarizer.apply(qualified(candidate, clock), clock.now())

    assert candidate.summary is not None and "Calificado" in candidate.summary
    [error] = [e.detail for e in candidate.timeline if e.type == "summary_error"]
    assert "boom" in error["error"]
    assert [e.type for e in candidate.timeline].count("summary_generated") == 1


def test_prompt_is_versioned() -> None:
    prompt = load_summary_prompt(DEFAULT_PROMPT_PATH)

    assert prompt.version.startswith("v1-")
    assert "invent" in prompt.body.lower()
