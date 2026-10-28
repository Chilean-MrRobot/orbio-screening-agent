"""Agente guionado y escenarios de demo para llenar el CRM sin gastar en LLM (spec 013).

`DemoAgent` implementa `ScreeningAgent` con una tabla: cada mensaje conocido del candidato
tiene su extracción. El resto del flujo (intake, orquestador, reglas, puntaje, canal y
`timeline`) es el real. `create_demo_app` levanta la API con este agente y el canal de
consola; `scripts/seed_demo.py` le envía los escenarios por HTTP.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from fastapi import FastAPI
from pydantic import SecretStr

from screening.adapters.llm_pricing import PricingTable, load_pricing
from screening.adapters.memory import InMemoryInterviewSlotStore
from screening.api.app import create_app
from screening.api.crm import build_crm_router
from screening.api.settings import WhatsAppSettings
from screening.api.wiring import build_storage
from screening.channels import ConsoleChannel, InboundBatch, InboundMessage
from screening.clock import Clock, SystemClock
from screening.config import AppConfig, load_config
from screening.conversation import ConversationOrchestrator
from screening.conversation.stages import CONFIRMATION_FIELD
from screening.crm.transitions import StatusChangeRequest, apply_status_change
from screening.domain import Candidate, CandidateStatus, Message, ScreeningData
from screening.ports import (
    CandidateRepository,
    ExtractionOutcome,
    LLMUsage,
    ReplyOutcome,
    ReplyRequest,
)
from screening.scheduling import InterviewScheduler

logger = logging.getLogger(__name__)

# Valores por defecto solo para el demo local; en Cloud Run se definen siempre.
DEMO_DEFAULTS = {
    "WHATSAPP_VERIFY_TOKEN": "demo-verify",
    "WHATSAPP_APP_SECRET": "demo-secret",
    "PANEL_PASSWORD": "demo",
}
# Prompt típico de un turno: sirve para estimar los tokens de entrada.
_PROMPT_CHARS = 2400
_CHARS_PER_TOKEN = 4


def demo_env(env: Mapping[str, str] = os.environ) -> dict[str, str]:
    return {**DEMO_DEFAULTS, **{k: v for k, v in env.items() if v}}


# --- Escenarios -------------------------------------------------------------------


@dataclass(frozen=True)
class DemoTurn:
    """Un mensaje del candidato y lo que el agente "entiende" de él."""

    text: str
    fields: dict[str, Any] = field(default_factory=dict)
    start_in_days: int | None = None
    confirm: bool = False
    human: bool = False
    opt_out: bool = False
    slot_choice: int | None = None  # spec 021: número del horario ofrecido que elige
    reject_slots: bool = False


@dataclass(frozen=True)
class RecruiterStep:
    status: CandidateStatus
    reason: str | None = None
    recruiter: str = "Marta (recruiter)"


@dataclass(frozen=True)
class DemoScenario:
    key: str
    application: dict[str, Any]
    turns: tuple[DemoTurn, ...] = ()
    recruiter: tuple[RecruiterStep, ...] = ()
    expected_status: CandidateStatus = CandidateStatus.QUALIFIED


CONFIRM = DemoTurn("Sí, todo correcto 👍", confirm=True)


def _application(key: str, name: str, phone: str, country: str, city: str) -> dict[str, Any]:
    return {
        "application_id": f"demo-{key}",
        "source": "infojobs" if country == "ES" else "occ",
        "full_name": name,
        "phone": phone,
        "country": country,
        "city": city,
        "vacancy_id": f"repartidor-{country.lower()}",
    }


def _welcome(name: str) -> DemoTurn:
    return DemoTurn(
        f"Hola! Sí, tengo unos minutos. Soy {name} y acepto el tratamiento de mis datos",
        {"full_name": name, "consent": True},
    )


DEMO_SCENARIOS: tuple[DemoScenario, ...] = (
    DemoScenario(
        "es-calificada-alta",
        _application("es-1", "Lucía Fernández", "+34611000001", "ES", "Madrid"),
        (
            _welcome("Lucía Fernández"),
            DemoTurn(
                "Tengo carnet B desde hace 6 años, coche propio y vivo en Madrid",
                {
                    "has_license": True,
                    "license_type": "B",
                    "license_years": 6,
                    "vehicle_type": "car",
                    "owns_vehicle": True,
                    "city": "Madrid",
                },
            ),
            DemoTurn(
                "Llevo 3 años repartiendo en Glovo. Puedo a tiempo completo, horario flexible, "
                "empiezo el lunes y tengo permiso de trabajo",
                {
                    "delivery_experience": {"years": 3, "platforms": ["Glovo"]},
                    "availability": "full_time",
                    "preferred_schedule": "flexible",
                    "work_permit": True,
                },
                start_in_days=3,
            ),
            CONFIRM,
            DemoTurn("El segundo me viene perfecto", slot_choice=2),
        ),
        (),
        CandidateStatus.INTERVIEW,
    ),
    DemoScenario(
        "es-calificado-medio",
        _application("es-2", "Marc Puig", "+34611000002", "ES", "Barcelona"),
        (
            _welcome("Marc Puig"),
            DemoTurn(
                "Carnet A2, moto propia, en Barcelona",
                {
                    "has_license": True,
                    "license_type": "A2",
                    "vehicle_type": "motorcycle",
                    "owns_vehicle": True,
                    "city": "Barcelona",
                },
            ),
            DemoTurn(
                "1 año en Just Eat. Busco media jornada, por las tardes-noches. Podría empezar "
                "en 10 días y sí tengo permiso",
                {
                    "delivery_experience": {"years": 1, "platforms": ["Just Eat"]},
                    "availability": "part_time",
                    "preferred_schedule": "evening",
                    "work_permit": True,
                },
                start_in_days=10,
            ),
            CONFIRM,
        ),
    ),
    DemoScenario(
        "mx-contratada",
        _application("mx-1", "Ana López", "+525510000001", "MX", "CDMX"),
        (
            _welcome("Ana López"),
            DemoTurn(
                "Tengo licencia de motociclista, moto propia y vivo en CDMX",
                {
                    "has_license": True,
                    "license_type": "motociclista",
                    "vehicle_type": "motorcycle",
                    "owns_vehicle": True,
                    "city": "Ciudad de México",
                },
            ),
            DemoTurn(
                "2 años con Rappi y DiDi Food, tiempo completo en las mañanas, puedo empezar "
                "esta semana. Sí tengo permiso",
                {
                    "delivery_experience": {"years": 2, "platforms": ["Rappi", "DiDi Food"]},
                    "availability": "full_time",
                    "preferred_schedule": "morning",
                    "work_permit": True,
                },
                start_in_days=4,
            ),
            CONFIRM,
        ),
        (RecruiterStep(CandidateStatus.INTERVIEW), RecruiterStep(CandidateStatus.HIRED)),
        CandidateStatus.HIRED,
    ),
    DemoScenario(
        "mx-calificado-bajo",
        _application("mx-2", "Diego Hernández", "+528110000002", "MX", "Monterrey"),
        (
            _welcome("Diego Hernández"),
            DemoTurn(
                "Licencia tipo A, repartiría en carro pero es de mi hermano. Estoy en Monterrey",
                {
                    "has_license": True,
                    "license_type": "A",
                    "vehicle_type": "car",
                    "owns_vehicle": False,
                    "city": "Monterrey",
                },
            ),
            DemoTurn(
                "No he trabajado de repartidor. Solo fines de semana, en la tarde, en unas 3 "
                "semanas. Sí tengo permiso",
                {
                    "delivery_experience": {"years": 0, "platforms": []},
                    "availability": "weekends",
                    "preferred_schedule": "afternoon",
                    "work_permit": True,
                },
                start_in_days=21,
            ),
            CONFIRM,
            DemoTurn("Ninguno me sirve, trabajo entre semana", reject_slots=True),
            DemoTurn("Tampoco puedo en esos, ¿no hay sábados?", reject_slots=True),
        ),
    ),
    DemoScenario(
        "es-sin-consentimiento",
        _application("es-3", "Javier Ruiz", "+34611000003", "ES", "Valencia"),
        (
            DemoTurn(
                "Soy Javier Ruiz, pero prefiero no compartir mis datos, no acepto",
                {"full_name": "Javier Ruiz", "consent": False},
            ),
        ),
        expected_status=CandidateStatus.DISQUALIFIED,
    ),
    DemoScenario(
        "es-sin-carnet",
        _application("es-4", "Sara Gómez", "+34611000004", "ES", "Sevilla"),
        (
            _welcome("Sara Gómez"),
            DemoTurn("No tengo carnet de conducir todavía", {"has_license": False}),
            # Spec 023: Sofía lo confirma antes de descartar.
            DemoTurn("No, no tengo", {"has_license": False}),
        ),
        expected_status=CandidateStatus.DISQUALIFIED,
    ),
    DemoScenario(
        "mx-licencia-no-habilita",
        _application("mx-3", "Luis Martínez", "+523310000003", "MX", "Guadalajara"),
        (
            _welcome("Luis Martínez"),
            DemoTurn(
                "Tengo licencia tipo B y reparto en moto, vivo en Guadalajara",
                {
                    "has_license": True,
                    "license_type": "B",
                    "vehicle_type": "motorcycle",
                    "owns_vehicle": True,
                    "city": "Guadalajara",
                },
            ),
            DemoTurn("Sí, tipo B, y en moto", {"license_type": "B", "vehicle_type": "motorcycle"}),
        ),
        expected_status=CandidateStatus.DISQUALIFIED,
    ),
    DemoScenario(
        "es-fuera-de-zona",
        _application("es-5", "Pablo Navarro", "+34611000005", "ES", "Salamanca"),
        (
            _welcome("Pablo Navarro"),
            DemoTurn(
                "Carnet B, coche propio, vivo en Salamanca",
                {
                    "has_license": True,
                    "license_type": "B",
                    "vehicle_type": "car",
                    "owns_vehicle": True,
                    "city": "Salamanca",
                },
            ),
            DemoTurn("Sí, en Salamanca", {"city": "Salamanca"}),
        ),
        expected_status=CandidateStatus.DISQUALIFIED,
    ),
    DemoScenario(
        "es-en-curso",
        _application("es-6", "Elena Torres", "+34611000006", "ES", "Zaragoza"),
        (
            _welcome("Elena Torres"),
            DemoTurn(
                "Tengo el B hace 4 años y coche propio. Vivo en Zaragoza",
                {
                    "has_license": True,
                    "license_type": "B",
                    "license_years": 4,
                    "vehicle_type": "car",
                    "owns_vehicle": True,
                    "city": "Zaragoza",
                },
            ),
        ),
        expected_status=CandidateStatus.IN_SCREENING,
    ),
    DemoScenario(
        "mx-sin-respuesta",
        _application("mx-4", "Carlos Ramírez", "+522210000004", "MX", "Puebla"),
        expected_status=CandidateStatus.CONTACTED,
    ),
    DemoScenario(
        "mx-pide-humano",
        _application("mx-5", "María Sánchez", "+524420000005", "MX", "Querétaro"),
        (
            _welcome("María Sánchez"),
            DemoTurn(
                "Antes de seguir, ¿puedo hablar con una persona? Tengo dudas sobre el pago",
                human=True,
            ),
        ),
        expected_status=CandidateStatus.IN_SCREENING,
    ),
    DemoScenario(
        "es-datos-invalidos",
        {**_application("es-7", "Iván Moreno", "12345", "ES", "Bilbao")},
        expected_status=CandidateStatus.INVALID_DATA,
    ),
)


# --- Agente guionado ----------------------------------------------------------------

_QUESTIONS: dict[str, str] = {
    "full_name": "¿Me confirmas tu nombre completo?",
    "consent": "¿Aceptas que usemos tus datos solo para este proceso de selección?",
    "has_license": "¿Tienes {license} vigente?",
    "license_type": "¿Qué tipo de {license} tienes?",
    "license_years": "¿Hace cuántos años la tienes?",
    "vehicle_type": "¿Repartirías en {motorcycle} o en {car}?",
    "owns_vehicle": "¿El vehículo es tuyo?",
    "city": "¿En qué ciudad vives?",
    "availability": "¿Qué disponibilidad tienes: tiempo completo, media jornada o fines de semana?",
    "preferred_schedule": "¿Qué horario prefieres: mañana, tarde, noche o flexible?",
    "delivery_experience": "¿Tienes experiencia como repartidor? ¿Cuánto tiempo y en qué apps?",
    "start_date": "¿Cuándo podrías empezar?",
    "work_permit": "¿Tienes permiso de trabajo vigente?",
}


class DemoAgent:
    """`ScreeningAgent` guionado por (teléfono, texto). Un texto desconocido no extrae nada."""

    def __init__(
        self,
        config: AppConfig,
        clock: Clock,
        scenarios: tuple[DemoScenario, ...] = DEMO_SCENARIOS,
        pricing: PricingTable | None = None,
    ) -> None:
        self._config = config
        self._clock = clock
        self._pricing = pricing or load_pricing()
        self._turns: dict[tuple[str, str], DemoTurn] = {}
        for scenario in scenarios:
            phone = scenario.application["phone"]
            for turn in scenario.turns:
                self._turns[(phone, turn.text)] = turn

    def extract(
        self, candidate: Candidate, new_messages: list[Message], expected_fields: list[str]
    ) -> ExtractionOutcome:
        outcome = ExtractionOutcome(language=candidate.language)
        fields: dict[str, Any] = {}
        for message in new_messages:
            turn = self._turns.get((candidate.phone or "", message.text))
            if turn is None:
                logger.info("Demo: mensaje sin guion de %s: %r", candidate.id, message.text)
                continue
            fields.update(turn.fields)
            if turn.start_in_days is not None:
                today = self._clock.now().date()
                fields["start_date"] = today + timedelta(days=turn.start_in_days)
            if turn.confirm:
                outcome.provided_fields.append(CONFIRMATION_FIELD)
            outcome.human_requested |= turn.human
            outcome.opt_out |= turn.opt_out
            outcome.slot_choice = turn.slot_choice or outcome.slot_choice
            outcome.slots_rejected |= turn.reject_slots
        outcome.updates = ScreeningData.model_validate(fields)
        outcome.provided_fields.extend(fields)
        prompt = "\n".join(m.text for m in new_messages)
        outcome.usage = [self._usage(self._config.llm.extraction_model, prompt, str(fields))]
        return outcome

    def reply(self, request: ReplyRequest) -> ReplyOutcome:
        candidate = request.candidate
        name = (candidate.data.full_name or candidate.application.full_name or "").split(" ")[0]
        if request.closing_summary:
            text = (
                f"¡Gracias, {name}! Repasemos tus datos:\n{_summary(candidate)}\n¿Está todo bien?"
            )
        else:
            fields = request.retry_fields or request.ask_fields
            question = self._question(fields[0], candidate) if fields else "¿Seguimos?"
            prefix = "Perdona, no te entendí bien. " if request.retry_fields else "¡Perfecto! "
            text = prefix + question
        history = "\n".join(m.text for m in candidate.messages[-6:])
        return ReplyOutcome(
            text=text, usage=[self._usage(self._config.llm.conversation_model, history, text)]
        )

    def _question(self, field_name: str, candidate: Candidate) -> str:
        template = _QUESTIONS.get(field_name, f"¿Me cuentas tu {field_name}?")
        region = self._config.regions.get(candidate.country) if candidate.country else None
        vocabulary = region.vocabulary if region else {}
        defaults = {"license": "licencia", "car": "coche", "motorcycle": "moto"}
        return template.format(**{**defaults, **vocabulary})

    def _usage(self, model: str, prompt: str, output: str) -> LLMUsage:
        input_tokens = (_PROMPT_CHARS + len(prompt)) // _CHARS_PER_TOKEN
        output_tokens = max(1, len(output) // _CHARS_PER_TOKEN)
        return LLMUsage(
            model=f"demo:{model}",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=0.0,
            cost_usd=self._pricing.cost(model, input_tokens, output_tokens),
        )


_WORDS = {
    "car": "coche",
    "motorcycle": "moto",
    "full_time": "tiempo completo",
    "part_time": "media jornada",
    "weekends": "fines de semana",
    "morning": "mañanas",
    "afternoon": "tardes",
    "evening": "noches",
    "flexible": "horario flexible",
}


def _summary(candidate: Candidate) -> str:
    data = candidate.data

    def word(value: Any) -> str:
        return _WORDS.get(str(value), str(value)) if value is not None else "—"

    lines = [
        f"• Licencia {data.license_type or '—'} · {word(data.vehicle_type)}"
        f"{' propio' if data.owns_vehicle else ''}",
        f"• Ciudad: {data.city or '—'}",
        f"• Disponibilidad: {word(data.availability)} ({word(data.preferred_schedule)})",
        f"• Inicio: {data.start_date.strftime('%d/%m') if data.start_date else '—'}",
    ]
    if data.delivery_experience:
        lines.append(f"• Experiencia: {data.delivery_experience.years:g} años")
    return "\n".join(lines)


# --- Seed en proceso y app de demo ------------------------------------------------


def seed_in_process(
    orchestrator: ConversationOrchestrator,
    repository: CandidateRepository,
    clock: Clock,
    scenarios: tuple[DemoScenario, ...] = DEMO_SCENARIOS,
    advance: Callable[[], None] = lambda: None,
) -> dict[str, str]:
    """Corre los escenarios por el orquestador sin HTTP. Devuelve `key → candidate_id`."""
    ids: dict[str, str] = {}
    for scenario in scenarios:
        result = orchestrator.start_application(dict(scenario.application))
        if result.candidate_id is None:
            continue
        ids[scenario.key] = result.candidate_id
        candidate = repository.get(result.candidate_id)
        assert candidate is not None
        for n, turn in enumerate(scenario.turns):
            advance()
            now = clock.now()
            message = InboundMessage(
                external_id=f"wamid.demo-{scenario.key}-{n}",
                sender=candidate.phone or "",
                text=turn.text,
                type="text",
                sent_at=now,
                received_at=now,
            )
            orchestrator.handle(InboundBatch(sender=message.sender, messages=[message]))
        for step in scenario.recruiter:
            advance()
            stored = repository.get(result.candidate_id)
            assert stored is not None
            request = StatusChangeRequest(
                status=step.status.value,  # type: ignore[arg-type]
                reason=step.reason,
                recruiter=step.recruiter,
            )
            apply_status_change(stored, request, clock.now())
            repository.save(stored)
    return ids


def create_demo_app(env: Mapping[str, str] | None = None) -> FastAPI:
    """`uvicorn screening.crm.demo:create_demo_app --factory`: la API con el `DemoAgent`.

    Siempre usa el canal de consola, aunque haya credenciales de WhatsApp: los teléfonos
    de demo son ficticios.
    """
    logging.basicConfig(level=logging.INFO)
    values = demo_env(os.environ if env is None else env)
    settings = WhatsAppSettings(
        verify_token=SecretStr(values["WHATSAPP_VERIFY_TOKEN"]),
        app_secret=SecretStr(values["WHATSAPP_APP_SECRET"]),
        batch_seconds=float(values.get("WHATSAPP_BATCH_SECONDS") or 0.5),
    )
    config = load_config()
    clock = SystemClock()
    repository, store = build_storage(values)
    scheduler = InterviewScheduler(config, InMemoryInterviewSlotStore(), clock)
    orchestrator = ConversationOrchestrator(
        repository=repository,
        agent=DemoAgent(config, clock),
        channel=ConsoleChannel(),
        clock=clock,
        config=config,
        scheduler=scheduler,
    )
    return create_app(
        settings,
        store=store,
        handler=orchestrator,
        clock=clock,
        applications=orchestrator,
        crm_router=build_crm_router(
            repository, clock, values["PANEL_PASSWORD"], scheduler=scheduler
        ),
    )
