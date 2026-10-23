"""Una extracción y una respuesta reales con OpenAI vía ADK. Se salta sin `OPENAI_API_KEY`."""

from __future__ import annotations

import os

import pytest

from screening.adapters.fake_knowledge import FakeKnowledgeBase
from screening.agent import AdkScreeningAgent
from screening.agent.guards import find_internal_leak
from screening.domain import Candidate, ChannelName, Message, MessageRole, Stage
from screening.ports import ReplyRequest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="falta OPENAI_API_KEY"),
]


@pytest.fixture
def agent(app_config, clock) -> AdkScreeningAgent:
    knowledge = FakeKnowledgeBase([("faq/pagos.md", "Los pagos se hacen cada semana.")])
    return AdkScreeningAgent.from_env(app_config, knowledge, clock=clock)


def test_real_extraction_normalizes_license(agent: AdkScreeningAgent, candidate: Candidate) -> None:
    candidate.stage = Stage.KNOCKOUT
    new = [
        Message(
            role=MessageRole.CANDIDATE,
            text="tengo el carnet de coche hace 5 años",
            channel=ChannelName.WHATSAPP,
        )
    ]
    candidate.messages.extend(new)

    outcome = agent.extract(candidate, new, ["has_license", "license_type"])

    assert outcome.updates.license_type == "B"
    assert outcome.updates.license_years == 5
    assert outcome.usage and outcome.usage[0].input_tokens > 0
    assert outcome.usage[0].cost_usd > 0


def test_real_reply_asks_one_clean_question(agent: AdkScreeningAgent, candidate: Candidate) -> None:
    request = ReplyRequest(candidate=candidate, stage=Stage.KNOCKOUT, ask_fields=["has_license"])

    outcome = agent.reply(request)

    assert outcome.text.count("?") == 1
    assert find_internal_leak(outcome.text) is None
    assert outcome.usage
