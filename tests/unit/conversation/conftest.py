"""Fakes del agente y del canal para probar el orquestador sin LLM ni red."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta
from itertools import count
from typing import Any

import pytest

from screening.adapters.memory import InMemoryCandidateRepository
from screening.channels import InboundBatch, InboundMessage
from screening.clock import FixedClock
from screening.config import AppConfig
from screening.conversation import ConversationOrchestrator
from screening.domain import Candidate, ChannelName, Message
from screening.ports import (
    ExtractionOutcome,
    LLMUsage,
    OutboundMessage,
    ReplyOutcome,
    ReplyRequest,
    SendResult,
)

USAGE = LLMUsage("gpt-5-mini", 100, 20, 500.0, 0.0002)

APPLICATION = {
    "application_id": "app-001",
    "source": "infojobs",
    "full_name": "Lucía Pérez",
    "phone": "+34600111222",
    "country": "ES",
    "city": "Madrid",
}
PHONE = "+34600111222"


@dataclass
class ExtractCall:
    candidate: Candidate
    new_messages: list[Message]
    expected_fields: list[str]


class ScriptedAgent:
    """`ScreeningAgent` guionado: devuelve las extracciones en orden y registra las llamadas.

    Un elemento del guion que sea una `Exception` se lanza. `reply` devuelve un texto que
    describe el pedido, o lanza si `fail_reply` es verdadero.
    """

    def __init__(self, *extractions: ExtractionOutcome | Exception) -> None:
        self._script: deque[ExtractionOutcome | Exception] = deque(extractions)
        self.extract_calls: list[ExtractCall] = []
        self.reply_calls: list[ReplyRequest] = []
        self.fail_reply = False

    def queue(self, *extractions: ExtractionOutcome | Exception) -> None:
        self._script.extend(extractions)

    def extract(
        self, candidate: Candidate, new_messages: list[Message], expected_fields: list[str]
    ) -> ExtractionOutcome:
        self.extract_calls.append(ExtractCall(candidate, new_messages, list(expected_fields)))
        item = self._script.popleft() if self._script else ExtractionOutcome()
        if isinstance(item, Exception):
            raise item
        return item

    def reply(self, request: ReplyRequest) -> ReplyOutcome:
        self.reply_calls.append(request)
        if self.fail_reply:
            raise RuntimeError("reply boom")
        text = f"[{request.stage}] ask={request.ask_fields} retry={request.retry_fields}"
        return ReplyOutcome(text=text, usage=[USAGE])


@dataclass
class RecordingChannel:
    name: ChannelName = ChannelName.WHATSAPP
    sent: list[OutboundMessage] = field(default_factory=list)
    fail: bool = False
    raise_error: bool = False
    _ids: Any = field(default_factory=lambda: count(1))

    def send(self, message: OutboundMessage) -> SendResult:
        if self.raise_error:
            raise RuntimeError("channel boom")
        if self.fail:
            return SendResult(ok=False, error="HTTP 500: boom")
        self.sent.append(message)
        return SendResult(ok=True, external_id=f"wamid.out-{next(self._ids)}")

    @property
    def texts(self) -> list[str]:
        return [m.text for m in self.sent]


class Inbox:
    """Arma lotes entrantes con IDs únicos y la hora del reloj."""

    def __init__(self, clock: FixedClock) -> None:
        self._clock = clock
        self._ids = count(1)

    def batch(
        self, *texts: str, sender: str = PHONE, profile_name: str | None = None
    ) -> InboundBatch:
        self._clock.advance(minutes=1)
        now = self._clock.now()
        messages = [
            InboundMessage(
                external_id=f"wamid.in-{next(self._ids)}",
                sender=sender,
                text=text,
                type="text",
                profile_name=profile_name,
                sent_at=now - timedelta(seconds=5),
                received_at=now,
            )
            for text in texts
        ]
        return InboundBatch(sender=sender, messages=messages)


@pytest.fixture
def agent() -> ScriptedAgent:
    return ScriptedAgent()


@pytest.fixture
def channel() -> RecordingChannel:
    return RecordingChannel()


@pytest.fixture
def inbox(clock: FixedClock) -> Inbox:
    return Inbox(clock)


@pytest.fixture
def orchestrator(
    repository: InMemoryCandidateRepository,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    clock: FixedClock,
    app_config: AppConfig,
) -> ConversationOrchestrator:
    return ConversationOrchestrator(
        repository=repository, agent=agent, channel=channel, clock=clock, config=app_config
    )


@pytest.fixture
def contacted(
    orchestrator: ConversationOrchestrator,
    repository: InMemoryCandidateRepository,
) -> Candidate:
    """Candidato español recién postulado y con el primer contacto enviado."""
    result = orchestrator.start_application(dict(APPLICATION))
    assert result.contacted
    candidate = repository.get_by_phone(PHONE)
    assert candidate is not None
    return candidate
