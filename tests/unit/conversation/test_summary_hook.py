"""Hook del resumen (spec 012) en el orquestador."""

from __future__ import annotations

from datetime import datetime

import pytest

from screening.adapters.memory import InMemoryCandidateRepository
from screening.clock import FixedClock
from screening.config import AppConfig
from screening.conversation import CONFIRMATION_FIELD, ConversationOrchestrator
from screening.domain import Candidate, CandidateStatus
from screening.ports import ExtractionOutcome
from tests.unit.conversation.conftest import APPLICATION, Inbox, RecordingChannel, ScriptedAgent
from tests.unit.conversation.test_orchestrator import advance_to_closing, extraction, saved


class RecordingSummarizer:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[CandidateStatus, int, datetime]] = []

    def apply(self, candidate: Candidate, at: datetime) -> None:
        self.calls.append((candidate.status, len(candidate.messages), at))
        if self.fail:
            raise RuntimeError("summary boom")
        candidate.summary = f"resumen {candidate.status}"


@pytest.fixture
def summarizer() -> RecordingSummarizer:
    return RecordingSummarizer()


@pytest.fixture
def orchestrator(
    repository: InMemoryCandidateRepository,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    clock: FixedClock,
    app_config: AppConfig,
    summarizer: RecordingSummarizer,
) -> ConversationOrchestrator:
    return ConversationOrchestrator(
        repository=repository,
        agent=agent,
        channel=channel,
        clock=clock,
        config=app_config,
        summarizer=summarizer,
    )


def test_summary_is_generated_when_qualified_after_the_final_message(
    contacted, orchestrator, agent, inbox, repository, summarizer, channel, clock
) -> None:
    advance_to_closing(orchestrator, agent, inbox)
    assert summarizer.calls == []

    agent.queue(ExtractionOutcome(provided_fields=[CONFIRMATION_FIELD]))
    orchestrator.handle(inbox.batch("todo correcto"))

    candidate = saved(repository)
    assert candidate.status == CandidateStatus.QUALIFIED
    assert candidate.summary == "resumen qualified"
    [(status, message_count, at)] = summarizer.calls
    assert status == CandidateStatus.QUALIFIED
    assert message_count == len(candidate.messages)  # el mensaje final ya salió
    assert candidate.messages[-1].text == channel.texts[-1]
    assert at == clock.now()


def test_summary_is_generated_when_disqualified(
    contacted, orchestrator, agent, inbox, repository, summarizer
) -> None:
    agent.queue(extraction(consent=True, has_license=False))
    orchestrator.handle(inbox.batch("no tengo carnet"))

    candidate = saved(repository)
    assert candidate.status == CandidateStatus.DISQUALIFIED
    assert candidate.summary == "resumen disqualified"
    assert len(summarizer.calls) == 1


def test_failing_summarizer_does_not_block_the_closing(
    contacted, orchestrator, agent, inbox, repository, summarizer, channel
) -> None:
    summarizer.fail = True
    advance_to_closing(orchestrator, agent, inbox)

    agent.queue(ExtractionOutcome(provided_fields=[CONFIRMATION_FIELD]))
    orchestrator.handle(inbox.batch("ok"))

    candidate = saved(repository)
    assert candidate.status == CandidateStatus.QUALIFIED
    assert candidate.summary is None
    assert any(e.type == "summary_error" for e in candidate.timeline)
    assert not any(e.type == "orchestrator_error" for e in candidate.timeline)
    assert channel.texts[-1].startswith("¡Listo")


def test_without_summarizer_nothing_happens(repository, agent, channel, clock, app_config) -> None:
    orchestrator = ConversationOrchestrator(
        repository=repository, agent=agent, channel=channel, clock=clock, config=app_config
    )
    inbox = Inbox(clock)
    orchestrator.start_application(dict(APPLICATION))
    agent.queue(extraction(consent=True, has_license=False))
    orchestrator.handle(inbox.batch("no tengo carnet"))

    candidate = saved(repository)
    assert candidate.status == CandidateStatus.DISQUALIFIED
    assert candidate.summary is None
