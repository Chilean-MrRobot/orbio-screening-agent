"""Fichas en silencio, canales falsos y un arnés para correr pasadas a una hora dada."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from screening.adapters.memory import InMemoryCandidateRepository
from screening.clock import FixedClock
from screening.config import AppConfig
from screening.domain import (
    ApplicationPayload,
    Candidate,
    CandidateStatus,
    ChannelName,
    Country,
    Language,
    Message,
    MessageRole,
    Stage,
    TimelineEvent,
)
from screening.reengagement import ReengagementReport, run_due_touches
from tests.unit.conversation.conftest import RecordingChannel

ES_PHONE = "+34600111222"
MX_PHONE = "+525512345678"

# Lunes 19/10/2026 10:00 UTC: 12:00 en Madrid (CEST) y 04:00 en Ciudad de México.
MONDAY = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)


def _application(phone: str | None, country: Country) -> ApplicationPayload:
    return ApplicationPayload(
        application_id=f"app-{phone}",
        source="infojobs",
        full_name="Lucía Pérez",
        phone=phone,
        country=country,
        city="Madrid" if country == Country.ES else "Ciudad de México",
    )


def contacted(
    at: datetime,
    *,
    phone: str | None = ES_PHONE,
    country: Country = Country.ES,
    language: Language = Language.ES,
) -> Candidate:
    """Ficha a la que el orquestador le envió el `first_contact` a la hora `at`."""
    candidate = Candidate(
        application=_application(phone, country),
        phone=phone,
        country=country,
        language=language,
        status=CandidateStatus.CONTACTED,
        created_at=at,
        updated_at=at,
    )
    candidate.messages.append(
        Message(role=MessageRole.AGENT, text="¡Hola!", channel=ChannelName.WHATSAPP, created_at=at)
    )
    candidate.last_agent_message_at = at
    candidate.record_event(at, "message_sent", template="first_contact", external_id="wamid.1")
    return candidate


def abandoned(
    at: datetime,
    *,
    phone: str = ES_PHONE,
    country: Country = Country.ES,
    stage: Stage = Stage.KNOCKOUT,
) -> Candidate:
    """Ficha en screening: el candidato escribió y el agente le respondió a la hora `at`."""
    candidate = Candidate(
        application=_application(phone, country),
        phone=phone,
        country=country,
        status=CandidateStatus.IN_SCREENING,
        stage=stage,
        created_at=at - timedelta(hours=1),
        updated_at=at,
    )
    candidate.messages += [
        Message(
            role=MessageRole.CANDIDATE,
            text="hola",
            channel=ChannelName.WHATSAPP,
            created_at=at - timedelta(minutes=5),
        ),
        Message(
            role=MessageRole.AGENT,
            text="¿Tienes licencia?",
            channel=ChannelName.WHATSAPP,
            created_at=at,
        ),
    ]
    candidate.last_candidate_message_at = at - timedelta(minutes=5)
    candidate.last_agent_message_at = at
    return candidate


class Harness:
    """Corre pasadas moviendo el `FixedClock` y deja a mano lo enviado y la ficha."""

    def __init__(
        self, repository: InMemoryCandidateRepository, clock: FixedClock, config: AppConfig
    ) -> None:
        self.repository = repository
        self.clock = clock
        self.config = config
        self.whatsapp = RecordingChannel(name=ChannelName.WHATSAPP)
        self.sms = RecordingChannel(name=ChannelName.SMS)
        self.channels = {ChannelName.WHATSAPP: self.whatsapp, ChannelName.SMS: self.sms}

    def add(self, candidate: Candidate) -> str:
        self.repository.save(candidate)
        return candidate.id

    def run_at(self, when: datetime) -> ReengagementReport:
        self.clock.advance(seconds=(when - self.clock.now()).total_seconds())
        return run_due_touches(
            repository=self.repository,
            channels=self.channels,
            clock=self.clock,
            config=self.config,
        )

    def get(self, candidate_id: str) -> Candidate:
        candidate = self.repository.get(candidate_id)
        assert candidate is not None
        return candidate

    def events(self, candidate_id: str, type: str) -> list[TimelineEvent]:
        return [e for e in self.get(candidate_id).timeline if e.type == type]

    @property
    def templates(self) -> list[str | None]:
        """Plantillas enviadas por ambos canales, en orden de envío."""
        sent = [
            (e.at, e.detail["template"])
            for c in self.repository.list()
            for e in c.timeline
            if e.type == "reengagement_touch_sent"
        ]
        return [template for _, template in sorted(sent, key=lambda item: item[0])]


@pytest.fixture
def harness(
    repository: InMemoryCandidateRepository, clock: FixedClock, app_config: AppConfig
) -> Harness:
    clock.advance(seconds=(MONDAY - clock.now()).total_seconds())
    return Harness(repository, clock, app_config)


def with_cadences(config: AppConfig, **updates: object) -> AppConfig:
    """Copia de la configuración con campos de `cadences` cambiados."""
    return config.model_copy(update={"cadences": config.cadences.model_copy(update=updates)})


def with_step_hours(config: AppConfig, trigger: str, hours: list[float]) -> AppConfig:
    """Copia con los `after_hours` de una cadencia reemplazados (en orden)."""
    cadences = [
        c.model_copy(
            update={
                "steps": [
                    s.model_copy(update={"after_hours": h})
                    for s, h in zip(c.steps, hours, strict=True)
                ]
            }
        )
        if c.trigger == trigger
        else c
        for c in config.cadences.cadences
    ]
    return with_cadences(config, cadences=cadences)
