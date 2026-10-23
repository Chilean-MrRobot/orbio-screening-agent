import asyncio

import pytest

from screening.adapters.fake_knowledge import FakeKnowledgeBase
from screening.agent.adk_agent import _faq_tool
from screening.agent.context import NO_NEW_MESSAGES
from screening.agent.guards import find_internal_leak
from screening.domain import (
    Candidate,
    ChannelName,
    Country,
    Language,
    Message,
    MessageRole,
    Stage,
)
from screening.ports import KnowledgeSnippet, LLMError, ReplyRequest
from tests.unit.agent.fakes import contents, system_text, tool_call


def add(candidate: Candidate, role: MessageRole, text: str) -> None:
    candidate.messages.append(Message(role=role, text=text, channel=ChannelName.WHATSAPP))


def request_for(candidate: Candidate, **kwargs) -> ReplyRequest:
    return ReplyRequest(candidate=candidate, stage=Stage.KNOCKOUT, **kwargs)


def test_reply_builds_instruction_and_history(make_agent, candidate: Candidate) -> None:
    agent, _, model = make_agent(reply=["¡Genial! ¿Repartirías en moto o en coche?"])
    add(candidate, MessageRole.AGENT, "¿Tienes carnet de conducir?")
    add(candidate, MessageRole.SYSTEM, "regla evaluada")
    add(candidate, MessageRole.CANDIDATE, "sí")
    add(candidate, MessageRole.CANDIDATE, "el B")

    outcome = agent.reply(request_for(candidate, ask_fields=["vehicle_type", "city"]))

    assert outcome.text == "¡Genial! ¿Repartirías en moto o en coche?"
    assert len(outcome.usage) == 1
    request = model.requests[0]
    system = system_text(request)
    assert "carnet de conducir" in system
    assert "Pregunta el dato `vehicle_type`" in system
    assert "## Guía por dato" in system
    assert contents(request) == [("model", "¿Tienes carnet de conducir?"), ("user", "sí\nel B")]


def test_reply_uses_vocabulary_of_mexico(make_agent, candidate: Candidate) -> None:
    candidate.country = Country.MX
    agent, _, model = make_agent(reply=["¿Tienes licencia de manejo vigente?"])

    agent.reply(request_for(candidate, ask_fields=["has_license"]))

    system = system_text(model.requests[0])
    assert '"licencia de manejo"' in system
    assert '"carro"' in system


def test_reply_without_new_messages_sends_marker(make_agent, candidate: Candidate) -> None:
    agent, _, model = make_agent(reply=["¿Seguimos?"])
    add(candidate, MessageRole.AGENT, "¿Tienes carnet?")

    agent.reply(request_for(candidate, ask_fields=["has_license"]))

    assert contents(model.requests[0])[-1] == ("user", NO_NEW_MESSAGES)


def test_question_is_answered_with_the_faq_tool(
    make_agent, candidate: Candidate, knowledge: FakeKnowledgeBase
) -> None:
    agent, _, model = make_agent(
        reply=[
            tool_call("search_faq", query="¿Cómo son los pagos?"),
            "Pagamos cada semana por transferencia. ¿Tienes carnet de conducir?",
        ]
    )
    add(candidate, MessageRole.CANDIDATE, "¿cuándo pagan?")

    outcome = agent.reply(
        request_for(candidate, question="¿Cuándo pagan?", ask_fields=["has_license"])
    )

    assert knowledge.queries == ["¿Cómo son los pagos?"]
    tool_result = contents(model.requests[1])[-1][1]
    assert "cada semana por transferencia" in tool_result
    assert '"found": true' in tool_result
    assert outcome.text.startswith("Pagamos cada semana")
    assert len(outcome.usage) == 2


def test_faq_tool_reports_not_found_and_survives_failures() -> None:
    class Broken:
        def search(self, query: str, *, limit: int = 3) -> list[KnowledgeSnippet]:
            raise RuntimeError("índice caído")

    assert _faq_tool(FakeKnowledgeBase())("¿Dan uniforme?") == {"found": False, "results": []}
    assert _faq_tool(Broken())("¿Dan uniforme?") == {"found": False, "results": []}
    assert _faq_tool(FakeKnowledgeBase()).__name__ == "search_faq"


@pytest.mark.parametrize(
    "text",
    [
        "Anoté tu license_type, ¿y tu ciudad?",
        "Voy a usar search_faq para ver eso",
        "¿Prefieres full_time?",
        "Hola {{first_name}}",
    ],
)
def test_internal_leaks_are_detected(text: str) -> None:
    assert find_internal_leak(text)


@pytest.mark.parametrize(
    "text",
    [
        "¿En qué ciudad vives?",
        "¿Nos das tu consentimiento para seguir?",
        "¿Jornada completa, media jornada o fines de semana?",
    ],
)
def test_clean_messages_pass_the_leak_filter(text: str) -> None:
    assert find_internal_leak(text) is None


def test_leaking_reply_is_retried(make_agent, candidate: Candidate) -> None:
    agent, _, model = make_agent(reply=["Anoté tu license_type.", "¿En qué ciudad vives?"])

    outcome = agent.reply(request_for(candidate, ask_fields=["city"]))

    assert outcome.text == "¿En qué ciudad vives?"
    assert len(model.requests) == 2
    assert len(outcome.usage) == 2


def test_reply_that_keeps_leaking_raises_llm_error(make_agent, candidate: Candidate) -> None:
    agent, _, _ = make_agent(reply=["Tu start_date?", "   "])

    with pytest.raises(LLMError):
        agent.reply(request_for(candidate, ask_fields=["start_date"]))


def test_reply_model_failure_raises_llm_error(make_agent, candidate: Candidate) -> None:
    agent, _, _ = make_agent(reply=[TimeoutError("timeout")])

    with pytest.raises(LLMError, match="timeout"):
        agent.reply(request_for(candidate, ask_fields=["city"]))


def test_reply_works_inside_a_running_event_loop(make_agent, candidate: Candidate) -> None:
    candidate.language = Language.EN
    agent, _, _ = make_agent(reply=["Which city do you live in?"])

    async def inside_fastapi() -> str:
        return agent.reply(request_for(candidate, ask_fields=["city"])).text

    assert asyncio.run(inside_fastapi()) == "Which city do you live in?"
