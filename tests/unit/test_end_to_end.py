"""Punta a punta sin red: intake → orquestador → agente ADK real (modelo guionado) → entrevista."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from screening.adapters.fake_knowledge import FakeKnowledgeBase
from screening.adapters.llm_pricing import load_pricing
from screening.adapters.memory import InMemoryCandidateRepository
from screening.agent import AdkScreeningAgent
from screening.clock import FixedClock
from screening.config import AppConfig
from screening.conversation import ConversationOrchestrator
from screening.domain import CandidateStatus, Stage
from tests.unit.agent.fakes import ScriptedLlm
from tests.unit.conversation.conftest import APPLICATION, PHONE, Inbox, RecordingChannel

NO_FIELDS: dict[str, Any] = {
    "full_name": None,
    "consent": None,
    "has_license": None,
    "license_type": None,
    "license_years": None,
    "vehicle_type": None,
    "owns_vehicle": None,
    "city": None,
    "availability": None,
    "preferred_schedule": None,
    "delivery_experience": None,
    "start_date": None,
    "work_permit": None,
}


def extraction(
    summary_confirmed: bool = False,
    slot_choice: int | None = None,
    slots_rejected: bool = False,
    **fields: Any,
) -> dict[str, Any]:
    return {
        "fields": {**NO_FIELDS, **fields},
        "invalid_fields": [],
        "language": "es",
        "sentiment": "neutral",
        "opt_out": False,
        "human_requested": False,
        "question": None,
        "summary_confirmed": summary_confirmed,
        "slot_choice": slot_choice,
        "slots_rejected": slots_rejected,
    }


def test_application_to_interview_with_the_adk_agent(
    app_config: AppConfig, clock: FixedClock, repository: InMemoryCandidateRepository
) -> None:
    extraction_model = ScriptedLlm(
        script=[
            extraction(consent=True),
            extraction(
                has_license=True,
                license_type="b",
                vehicle_type="car",
                owns_vehicle=True,
                city="madrid",
            ),
            extraction(
                delivery_experience={"years": 3, "platforms": ["Glovo"]},
                availability="full_time",
                preferred_schedule="flexible",
                start_date="2026-10-25",
                work_permit=True,
            ),
            extraction(summary_confirmed=True),
            extraction(slot_choice=2),
        ]
    )
    reply_model = ScriptedLlm(
        script=[
            "¡Gracias, Lucía! ¿Tienes carnet de conducir y en qué vehículo repartirías?",
            "¡Genial! ¿Cuánta experiencia tienes repartiendo y qué disponibilidad tienes?",
            "Repasemos: carnet B, coche propio, Madrid, 3 años en Glovo. ¿Está todo bien?",
        ]
    )
    agent = AdkScreeningAgent(
        app_config,
        FakeKnowledgeBase([("faq/pago.md", "Se paga cada semana por transferencia.")]),
        clock=clock,
        pricing=load_pricing(),
        extraction_model=extraction_model,
        reply_model=reply_model,
        tracing=False,
    )
    channel = RecordingChannel()
    orchestrator = ConversationOrchestrator(
        repository=repository, agent=agent, channel=channel, clock=clock, config=app_config
    )
    inbox = Inbox(clock)

    assert orchestrator.start_application(dict(APPLICATION)).contacted
    for text in (
        "sí, acepto",
        "tengo carnet B, coche propio y vivo en Madrid",
        "3 años en Glovo, full time, horario flexible, empiezo el sábado y tengo permiso",
        "sí, todo correcto",
        "el segundo",
    ):
        orchestrator.handle(inbox.batch(text))

    candidate = repository.get_by_phone(PHONE)
    assert candidate is not None
    assert candidate.status == CandidateStatus.INTERVIEW
    assert candidate.stage == Stage.DONE
    # Jueves 22/10 10:00 UTC + 24 h de antelación; el lunes 26 ya es horario de invierno.
    assert candidate.interview is not None
    assert candidate.interview.start == datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
    assert candidate.data.license_type == "B"
    assert candidate.score is not None and candidate.score.total == 100
    assert channel.texts[0].startswith("¡Hola Lucía! Soy Sofía")
    assert channel.texts[1:4] == [
        "¡Gracias, Lucía! ¿Tienes carnet de conducir y en qué vehículo repartirías?",
        "¡Genial! ¿Cuánta experiencia tienes repartiendo y qué disponibilidad tienes?",
        "Repasemos: carnet B, coche propio, Madrid, 3 años en Glovo. ¿Está todo bien?",
    ]
    offer = channel.texts[-2]
    assert offer.startswith("¡Listo, Lucía!")
    assert "1) viernes 23 de octubre, 12:30\n2) lunes 26 de octubre, 10:00" in offer
    assert channel.texts[-1].startswith("¡Agendado, Lucía! ✅ Tu entrevista es el lunes 26")
    assert extraction_model.script == [] and reply_model.script == []
