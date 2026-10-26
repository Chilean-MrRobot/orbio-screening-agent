"""El orquestador cancela el calentamiento y agenda el próximo toque (specs 011 y 017)."""

from __future__ import annotations

from datetime import timedelta

from screening.adapters.memory import InMemoryCandidateRepository
from screening.channels import SimulatedSmsChannel
from screening.clock import FixedClock
from screening.config import AppConfig
from screening.conversation import CONFIRMATION_FIELD, ConversationOrchestrator
from screening.domain import Candidate, CandidateStatus, ChannelName, ScreeningData
from screening.ports import ExtractionOutcome
from screening.reengagement import ReengagementReport, run_due_touches
from tests.unit.conversation.conftest import (
    APPLICATION,
    PHONE,
    Inbox,
    RecordingChannel,
    ScriptedAgent,
)
from tests.unit.conversation.test_orchestrator import advance_to_closing


def _run(
    repository: InMemoryCandidateRepository,
    channel: RecordingChannel,
    clock: FixedClock,
    config: AppConfig,
) -> ReengagementReport:
    channels = {ChannelName.WHATSAPP: channel, ChannelName.SMS: SimulatedSmsChannel(clock)}
    return run_due_touches(repository=repository, channels=channels, clock=clock, config=config)


def _reload(repository: InMemoryCandidateRepository, candidate: Candidate) -> Candidate:
    stored = repository.get(candidate.id)
    assert stored is not None
    return stored


def test_reply_cancels_pending_touches_and_new_silence_restarts(
    orchestrator: ConversationOrchestrator,
    repository: InMemoryCandidateRepository,
    channel: RecordingChannel,
    clock: FixedClock,
    inbox: Inbox,
    app_config: AppConfig,
    contacted: Candidate,
) -> None:
    clock.advance(hours=24)
    _run(repository, channel, clock, app_config)
    assert _reload(repository, contacted).reengagement.cadence == "never_responded"

    orchestrator.handle(inbox.batch("hola, sí me interesa"))
    replied_at = clock.now()
    candidate = _reload(repository, contacted)
    assert candidate.status == CandidateStatus.IN_SCREENING
    assert candidate.reengagement.cadence is None
    [cancelled] = [e for e in candidate.timeline if e.type == "reengagement_cancelled"]
    assert cancelled.detail == {"cadence": "never_responded"}
    # El agente respondió: queda agendado abandoned_mid_screening desde esa respuesta.
    assert candidate.reengagement.next_touch_at == replied_at + timedelta(hours=24)

    # El candidato vuelve a callar: la pasada no lo lee hasta que vence.
    clock.advance(hours=1)
    assert _run(repository, channel, clock, app_config).read == 0

    stage_before = candidate.stage
    clock.advance(hours=23)
    report = _run(repository, channel, clock, app_config)
    assert [t.step for t in report.touches] == ["resume_whatsapp"]
    candidate = _reload(repository, contacted)
    assert candidate.reengagement.cadence == "abandoned_mid_screening"
    assert candidate.stage == stage_before


def test_first_contact_schedules_the_first_reminder(
    clock: FixedClock, repository: InMemoryCandidateRepository, contacted: Candidate
) -> None:
    assert contacted.status == CandidateStatus.CONTACTED
    assert contacted.reengagement.next_touch_at == clock.now() + timedelta(hours=24)


def test_failed_first_contact_is_due_right_away(
    orchestrator: ConversationOrchestrator,
    repository: InMemoryCandidateRepository,
    channel: RecordingChannel,
    clock: FixedClock,
) -> None:
    channel.fail = True

    result = orchestrator.start_application(dict(APPLICATION))

    assert not result.contacted
    candidate = repository.get_by_phone(PHONE)
    assert candidate is not None
    assert candidate.status == CandidateStatus.NEW
    assert candidate.reengagement.next_touch_at == clock.now()


def test_opt_out_leaves_no_touch(
    orchestrator: ConversationOrchestrator,
    repository: InMemoryCandidateRepository,
    agent: ScriptedAgent,
    inbox: Inbox,
    contacted: Candidate,
) -> None:
    agent.fail_reply = True
    orchestrator.handle(inbox.batch("hola"))
    candidate = _reload(repository, contacted)
    # Falló la respuesta pero salió la disculpa: el agente habló último y se agenda.
    assert candidate.reengagement.next_touch_at is not None

    agent.queue(ExtractionOutcome(opt_out=True))
    orchestrator.handle(inbox.batch("no me escriban más"))

    candidate = _reload(repository, contacted)
    assert candidate.status == CandidateStatus.OPTED_OUT
    assert candidate.reengagement.next_touch_at is None


def test_qualified_candidate_leaves_no_touch(
    orchestrator: ConversationOrchestrator,
    repository: InMemoryCandidateRepository,
    agent: ScriptedAgent,
    inbox: Inbox,
    contacted: Candidate,
) -> None:
    advance_to_closing(orchestrator, agent, inbox)
    assert _reload(repository, contacted).reengagement.next_touch_at is not None

    agent.queue(ExtractionOutcome(provided_fields=[CONFIRMATION_FIELD]))
    orchestrator.handle(inbox.batch("sí, todo correcto"))

    candidate = _reload(repository, contacted)
    assert candidate.status == CandidateStatus.QUALIFIED
    assert candidate.reengagement.next_touch_at is None


def test_disqualified_candidate_leaves_no_touch(
    orchestrator: ConversationOrchestrator,
    repository: InMemoryCandidateRepository,
    agent: ScriptedAgent,
    inbox: Inbox,
    contacted: Candidate,
) -> None:
    agent.queue(
        ExtractionOutcome(updates=ScreeningData(has_license=False), provided_fields=["has_license"])
    )

    orchestrator.handle(inbox.batch("no tengo carnet"))

    candidate = _reload(repository, contacted)
    assert candidate.status == CandidateStatus.DISQUALIFIED
    assert candidate.reengagement.next_touch_at is None


def test_human_handoff_leaves_no_touch(
    orchestrator: ConversationOrchestrator,
    repository: InMemoryCandidateRepository,
    agent: ScriptedAgent,
    inbox: Inbox,
    contacted: Candidate,
) -> None:
    agent.queue(ExtractionOutcome(human_requested=True))

    orchestrator.handle(inbox.batch("quiero hablar con una persona"))

    candidate = _reload(repository, contacted)
    assert candidate.human_requested
    assert candidate.reengagement.next_touch_at is None


def test_reply_without_active_cadence_records_no_cancellation(
    orchestrator: ConversationOrchestrator,
    repository: InMemoryCandidateRepository,
    inbox: Inbox,
    contacted: Candidate,
) -> None:
    orchestrator.handle(inbox.batch("hola"))

    candidate = _reload(repository, contacted)
    assert not [e for e in candidate.timeline if e.type == "reengagement_cancelled"]
