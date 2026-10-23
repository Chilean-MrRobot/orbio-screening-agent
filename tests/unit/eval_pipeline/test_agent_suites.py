"""Objetivos de las suites del agente (009) con un agente falso: sin LLM."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest

from evals.dataset import load_suite
from evals.suites import SUITES
from evals.targets import extraction, reply
from evals.targets.common import total_usage
from screening.adapters.fake_llm import FakeLLMClient
from screening.clock import Clock
from screening.config import AppConfig
from screening.domain import (
    Country,
    Language,
    MessageRole,
    ScreeningData,
    Sentiment,
    Stage,
    VehicleType,
)
from screening.ports import (
    ExtractionOutcome,
    KnowledgeBase,
    LLMUsage,
    ReplyOutcome,
    ReplyRequest,
)


class RecordingAgent:
    def __init__(self, clock: Clock, knowledge: KnowledgeBase) -> None:
        self.clock = clock
        self.knowledge = knowledge
        self.extract_calls: list[tuple] = []
        self.reply_calls: list[ReplyRequest] = []

    def extract(self, candidate, new_messages, expected_fields) -> ExtractionOutcome:
        self.extract_calls.append((candidate, new_messages, expected_fields))
        return ExtractionOutcome(
            updates=ScreeningData(vehicle_type=VehicleType.CAR, needs_review=["x"]),
            provided_fields=["vehicle_type"],
            language=Language.EN,
            usage=[LLMUsage("gpt-5-mini", 10, 5, 100.0, 0.001)] * 2,
        )

    def reply(self, request: ReplyRequest) -> ReplyOutcome:
        self.reply_calls.append(request)
        return ReplyOutcome(text="¿En qué ciudad vives?")


@pytest.fixture
def agents() -> list[RecordingAgent]:
    return []


def factory(agents: list[RecordingAgent]):
    def make(config: AppConfig, clock: Clock, knowledge: KnowledgeBase) -> RecordingAgent:
        agents.append(RecordingAgent(clock, knowledge))
        return agents[-1]

    return make


@pytest.mark.parametrize("suite", ["extraction", "reply"])
def test_agent_suites_are_registered_and_load(suite: str) -> None:
    assert suite in SUITES
    cases = load_suite(suite)
    assert len(cases) >= 10
    assert all("question" not in case.input for case in cases)  # el juez debe ver todo el input


def test_extraction_target_builds_candidate_from_input(app_config, agents) -> None:
    case = next(c for c in load_suite("extraction") if c.id == "ex-20-correccion")
    target = extraction.make_target(FakeLLMClient(), app_config, factory(agents))

    output = target(case)

    [agent] = agents
    assert agent.clock.now() == datetime(2026, 10, 23, 12, tzinfo=UTC)
    candidate, new_messages, _ = agent.extract_calls[0]
    assert candidate.country == Country.ES
    assert candidate.stage == Stage.CLOSING
    assert candidate.data.vehicle_type == VehicleType.CAR
    assert [m.role for m in candidate.messages] == [MessageRole.AGENT, MessageRole.CANDIDATE]
    assert candidate.messages[-1] is new_messages[0]
    answer = json.loads(output.answer)
    assert answer["updates"] == {"vehicle_type": "car"}
    assert answer["language"] == "en"
    assert output.usage.input_tokens == 20
    assert output.usage.cost_usd == pytest.approx(0.002)


def test_reply_target_builds_request_and_faq(app_config, agents) -> None:
    case = next(c for c in load_suite("reply") if c.id == "rp-05-faq-propinas")
    target = reply.make_target(FakeLLMClient(), app_config, factory(agents))

    output = target(case)

    [agent] = agents
    [request] = agent.reply_calls
    assert request.question == "¿Las propinas son para el repartidor?"
    assert request.ask_fields == ["city"]
    assert request.candidate.sentiment == Sentiment.NEUTRAL
    assert agent.knowledge.search("propinas")[0].source == "faq/propinas.md"
    assert output.answer == "¿En qué ciudad vives?"
    assert output.usage is None


def test_reply_cases_cover_closing_and_human(app_config, agents) -> None:
    target = reply.make_target(FakeLLMClient(), app_config, factory(agents))
    for case in load_suite("reply"):
        target(case)

    requests = [a.reply_calls[0] for a in agents]
    assert any(r.closing_summary for r in requests)
    assert any(r.candidate.human_requested for r in requests)
    closing = next(r for r in requests if r.closing_summary)
    assert closing.candidate.data.start_date == date(2026, 10, 26)


def test_total_usage_sums_or_is_none() -> None:
    assert total_usage([]) is None
    usage = total_usage([LLMUsage("m", 1, 2, 3.0, 0.5), LLMUsage("m", 1, 2, 3.0, 0.5)])
    assert (usage.input_tokens, usage.output_tokens, usage.latency_ms, usage.cost_usd) == (
        2,
        4,
        6.0,
        1.0,
    )
