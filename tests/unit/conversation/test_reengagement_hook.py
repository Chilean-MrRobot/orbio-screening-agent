"""El orquestador cancela el calentamiento cuando el candidato responde (spec 011)."""

from __future__ import annotations

from datetime import timedelta

from screening.adapters.memory import InMemoryCandidateRepository
from screening.channels import SimulatedSmsChannel
from screening.clock import FixedClock
from screening.config import AppConfig
from screening.conversation import ConversationOrchestrator
from screening.domain import Candidate, CandidateStatus, ChannelName
from screening.reengagement import ReengagementReport, run_due_touches
from tests.unit.conversation.conftest import Inbox, RecordingChannel


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
    assert candidate.reengagement.next_touch_at is None
    [cancelled] = [e for e in candidate.timeline if e.type == "reengagement_cancelled"]
    assert cancelled.detail == {"cadence": "never_responded"}

    # El agente respondió y el candidato vuelve a callar: arranca abandoned_mid_screening.
    clock.advance(hours=1)
    report = _run(repository, channel, clock, app_config)
    assert report.touches == []
    candidate = _reload(repository, contacted)
    assert candidate.reengagement.cadence == "abandoned_mid_screening"
    assert candidate.reengagement.next_touch_at == replied_at + timedelta(hours=24)

    clock.advance(hours=23)
    report = _run(repository, channel, clock, app_config)
    assert [t.step for t in report.touches] == ["resume_whatsapp"]
    stage_before = candidate.stage
    assert _reload(repository, contacted).stage == stage_before


def test_reply_without_active_cadence_records_no_cancellation(
    orchestrator: ConversationOrchestrator,
    repository: InMemoryCandidateRepository,
    inbox: Inbox,
    contacted: Candidate,
) -> None:
    orchestrator.handle(inbox.batch("hola"))

    candidate = _reload(repository, contacted)
    assert not [e for e in candidate.timeline if e.type == "reengagement_cancelled"]
