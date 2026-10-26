"""Lease por candidato en el orquestador (spec 015)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from screening.adapters.memory import InMemoryCandidateRepository, InMemoryLeaseStore
from screening.api import WhatsAppSettings, create_app
from screening.channels import InboundBatch, InMemoryInboundMessageStore
from screening.clock import FixedClock
from screening.config import AppConfig
from screening.conversation import CandidateBusy, ConversationOrchestrator
from screening.domain import Candidate, Message
from screening.ports import ExtractionOutcome
from tests.unit.conversation.conftest import (
    APPLICATION,
    PHONE,
    Inbox,
    RecordingChannel,
    ScriptedAgent,
)

TTL = timedelta(seconds=120)


class RecordingLeaseStore(InMemoryLeaseStore):
    """`InMemoryLeaseStore` que registra cada intento y cada liberación."""

    def __init__(self) -> None:
        super().__init__()
        self.acquired: list[tuple[str, str, datetime, timedelta]] = []
        self.released: list[tuple[str, str]] = []

    def try_acquire(self, key: str, owner: str, *, now: datetime, ttl: timedelta) -> bool:
        ok = super().try_acquire(key, owner, now=now, ttl=ttl)
        if ok:
            self.acquired.append((key, owner, now, ttl))
        return ok

    def release(self, key: str, owner: str) -> None:
        self.released.append((key, owner))
        super().release(key, owner)

    def is_free(self, key: str, now: datetime) -> bool:
        return InMemoryLeaseStore.try_acquire(self, key, "probe", now=now, ttl=timedelta(0))


@pytest.fixture
def leases() -> RecordingLeaseStore:
    return RecordingLeaseStore()


def make_orchestrator(
    repository: InMemoryCandidateRepository,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    clock: FixedClock,
    config: AppConfig,
    leases: InMemoryLeaseStore,
) -> ConversationOrchestrator:
    return ConversationOrchestrator(
        repository=repository,
        agent=agent,
        channel=channel,
        clock=clock,
        config=config,
        leases=leases,
        lease_ttl=TTL,
    )


@pytest.fixture
def leased(
    repository: InMemoryCandidateRepository,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    clock: FixedClock,
    app_config: AppConfig,
    leases: RecordingLeaseStore,
) -> ConversationOrchestrator:
    return make_orchestrator(repository, agent, channel, clock, app_config, leases)


# --- Camino feliz y errores --------------------------------------------------------------


def test_lease_is_taken_and_released_on_success(
    leased: ConversationOrchestrator,
    leases: RecordingLeaseStore,
    inbox: Inbox,
    channel: RecordingChannel,
    clock: FixedClock,
) -> None:
    leased.handle(inbox.batch("hola"))

    [(key, owner, at, ttl)] = leases.acquired
    assert key == PHONE
    assert ttl == TTL
    assert at == clock.now()
    assert leases.released == [(PHONE, owner)]
    assert leases.is_free(PHONE, clock.now())
    assert channel.sent  # el turno corrió


def test_each_call_uses_a_different_owner(
    leased: ConversationOrchestrator, leases: RecordingLeaseStore, inbox: Inbox
) -> None:
    leased.handle(inbox.batch("hola"))
    leased.handle(inbox.batch("sigo aquí"))

    owners = [owner for _, owner, _, _ in leases.acquired]
    assert len(owners) == 2
    assert owners[0] != owners[1]


def test_lease_is_released_when_the_agent_fails(
    leased: ConversationOrchestrator,
    leases: RecordingLeaseStore,
    agent: ScriptedAgent,
    inbox: Inbox,
    clock: FixedClock,
) -> None:
    agent.queue(RuntimeError("llm boom"))

    leased.handle(inbox.batch("hola"))

    assert len(leases.released) == 1
    assert leases.is_free(PHONE, clock.now())


def test_lease_is_released_when_the_channel_raises(
    leased: ConversationOrchestrator,
    leases: RecordingLeaseStore,
    channel: RecordingChannel,
    inbox: Inbox,
    clock: FixedClock,
) -> None:
    channel.raise_error = True

    leased.handle(inbox.batch("hola"))

    assert len(leases.released) == 1
    assert leases.is_free(PHONE, clock.now())


def test_lease_is_released_when_the_repository_fails(
    agent: ScriptedAgent,
    channel: RecordingChannel,
    clock: FixedClock,
    app_config: AppConfig,
    leases: RecordingLeaseStore,
    inbox: Inbox,
) -> None:
    class BrokenRepository(InMemoryCandidateRepository):
        def get_by_phone(self, phone: str) -> Candidate | None:
            raise RuntimeError("firestore boom")

    orchestrator = make_orchestrator(BrokenRepository(), agent, channel, clock, app_config, leases)

    orchestrator.handle(inbox.batch("hola"))  # el error se registra, no se propaga

    assert len(leases.released) == 1
    assert leases.is_free(PHONE, clock.now())


def test_release_failure_does_not_propagate(
    repository: InMemoryCandidateRepository,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    clock: FixedClock,
    app_config: AppConfig,
    inbox: Inbox,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BrokenRelease(InMemoryLeaseStore):
        def release(self, key: str, owner: str) -> None:
            raise RuntimeError("release boom")

    orchestrator = make_orchestrator(repository, agent, channel, clock, app_config, BrokenRelease())

    orchestrator.handle(inbox.batch("hola"))

    assert channel.sent
    assert "release boom" in caplog.text


# --- Ocupado --------------------------------------------------------------------------


def test_busy_lease_raises_and_does_not_process(
    leased: ConversationOrchestrator,
    leases: RecordingLeaseStore,
    contacted: Candidate,
    repository: InMemoryCandidateRepository,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    inbox: Inbox,
    clock: FixedClock,
) -> None:
    sent_before = len(channel.sent)
    before = repository.get_by_phone(PHONE)
    leases.try_acquire(PHONE, "other-instance", now=clock.now(), ttl=TTL)
    batch = inbox.batch("hola")

    with pytest.raises(CandidateBusy) as excinfo:
        leased.handle(batch)

    assert excinfo.value.key == PHONE
    assert agent.extract_calls == []
    assert len(channel.sent) == sent_before
    assert repository.get_by_phone(PHONE) == before
    # No libera el lease ajeno.
    assert not leases.is_free(PHONE, clock.now())


def test_expired_lease_can_be_taken(
    leased: ConversationOrchestrator,
    leases: RecordingLeaseStore,
    agent: ScriptedAgent,
    inbox: Inbox,
    clock: FixedClock,
) -> None:
    leases.try_acquire(PHONE, "crashed-instance", now=clock.now(), ttl=TTL)
    clock.advance(seconds=TTL.total_seconds() + 1)

    leased.handle(inbox.batch("hola"))

    assert len(agent.extract_calls) == 1
    assert leases.is_free(PHONE, clock.now())


def test_two_orchestrators_do_not_process_the_same_candidate_at_once(
    repository: InMemoryCandidateRepository,
    channel: RecordingChannel,
    clock: FixedClock,
    app_config: AppConfig,
    leases: RecordingLeaseStore,
    inbox: Inbox,
) -> None:
    """El agente de A, en medio de su turno, le entrega a B un lote del mismo candidato."""
    second = make_orchestrator(repository, ScriptedAgent(), channel, clock, app_config, leases)
    concurrent = inbox.batch("¿sigues ahí?")
    busy: list[CandidateBusy] = []

    class InterleavingAgent(ScriptedAgent):
        def extract(
            self, candidate: Candidate, new_messages: list[Message], expected_fields: list[str]
        ) -> ExtractionOutcome:
            try:
                second.handle(concurrent)
            except CandidateBusy as exc:
                busy.append(exc)
            return super().extract(candidate, new_messages, expected_fields)

    first_agent = InterleavingAgent()
    first = make_orchestrator(repository, first_agent, channel, clock, app_config, leases)

    first.handle(inbox.batch("hola"))

    assert [e.key for e in busy] == [PHONE]
    assert len(first_agent.extract_calls) == 1
    candidate = repository.get_by_phone(PHONE)
    assert candidate is not None
    assert "¿sigues ahí?" not in [m.text for m in candidate.messages]
    # Ya libre, B procesa el lote que tuvo que reintentar.
    second.handle(concurrent)
    candidate = repository.get_by_phone(PHONE)
    assert candidate is not None
    assert "¿sigues ahí?" in [m.text for m in candidate.messages]


def test_other_candidates_are_not_blocked(
    leased: ConversationOrchestrator,
    leases: RecordingLeaseStore,
    agent: ScriptedAgent,
    inbox: Inbox,
    clock: FixedClock,
) -> None:
    leases.try_acquire(PHONE, "other-instance", now=clock.now(), ttl=TTL)

    leased.handle(inbox.batch("hola", sender="+34600999888"))

    assert len(agent.extract_calls) == 1


# --- Postulaciones ----------------------------------------------------------------------


def test_application_takes_and_releases_the_lease(
    leased: ConversationOrchestrator,
    leases: RecordingLeaseStore,
    clock: FixedClock,
) -> None:
    result = leased.start_application(dict(APPLICATION))

    assert result.contacted
    assert [key for key, *_ in leases.acquired] == [PHONE]
    assert len(leases.released) == 1
    assert leases.is_free(PHONE, clock.now())


def test_application_key_is_the_normalized_phone(
    leased: ConversationOrchestrator, leases: RecordingLeaseStore
) -> None:
    leased.start_application({**APPLICATION, "phone": "0034 600 111 222"})

    assert [key for key, *_ in leases.acquired] == [PHONE]


def test_application_with_busy_phone_raises_and_creates_nothing(
    leased: ConversationOrchestrator,
    leases: RecordingLeaseStore,
    repository: InMemoryCandidateRepository,
    channel: RecordingChannel,
    clock: FixedClock,
) -> None:
    leases.try_acquire(PHONE, "other-instance", now=clock.now(), ttl=TTL)

    with pytest.raises(CandidateBusy):
        leased.start_application(dict(APPLICATION))

    assert repository.get_by_phone(PHONE) is None
    assert channel.sent == []


def test_application_without_valid_phone_needs_no_lease(
    leased: ConversationOrchestrator, leases: RecordingLeaseStore
) -> None:
    result = leased.start_application({**APPLICATION, "phone": "abc"})

    assert not result.contacted
    assert leases.acquired == []


def test_busy_application_endpoint_returns_503(
    leased: ConversationOrchestrator, leases: RecordingLeaseStore, clock: FixedClock
) -> None:
    leases.try_acquire(PHONE, "other-instance", now=clock.now(), ttl=TTL)
    settings = WhatsAppSettings(
        verify_token=SecretStr("v"), app_secret=SecretStr("s"), phone_number_id="ID"
    )
    app = create_app(
        settings,
        store=InMemoryInboundMessageStore(),
        handler=leased,
        clock=clock,
        applications=leased,
    )

    response = TestClient(app).post("/applications", json=APPLICATION)

    assert response.status_code == 503
    assert response.headers["Retry-After"]


def test_default_lease_store_keeps_old_callers_working(
    repository: InMemoryCandidateRepository,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    clock: FixedClock,
    app_config: AppConfig,
    inbox: Inbox,
) -> None:
    orchestrator = ConversationOrchestrator(
        repository=repository, agent=agent, channel=channel, clock=clock, config=app_config
    )

    orchestrator.handle(inbox.batch("hola"))
    orchestrator.handle(InboundBatch(sender=PHONE, messages=inbox.batch("otra").messages))

    assert len(agent.extract_calls) == 2
