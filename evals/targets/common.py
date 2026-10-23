"""Piezas compartidas por los objetivos del agente (009): candidato, reloj y uso total."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time
from typing import Any

from screening.clock import Clock, FixedClock
from screening.config import AppConfig
from screening.domain import (
    ApplicationPayload,
    Candidate,
    ChannelName,
    Country,
    Language,
    Message,
    MessageRole,
    ScreeningData,
    Sentiment,
    Stage,
)
from screening.ports import KnowledgeBase, LLMUsage, ScreeningAgent

# (config, reloj, FAQ) → agente. En las evals es el agente real; en los tests, uno falso.
AgentFactory = Callable[[AppConfig, Clock, KnowledgeBase], ScreeningAgent]


def adk_agent_factory(config: AppConfig, clock: Clock, knowledge: KnowledgeBase) -> ScreeningAgent:
    from screening.agent import AdkScreeningAgent

    return AdkScreeningAgent.from_env(config, knowledge, clock=clock)


def clock_for(case_input: dict[str, Any]) -> FixedClock:
    """Mediodía UTC del `today` del caso, para que las fechas relativas sean reproducibles."""
    today = date.fromisoformat(case_input["today"])
    return FixedClock(datetime.combine(today, time(12, 0), tzinfo=UTC))


def candidate_from_input(case_input: dict[str, Any]) -> Candidate:
    country = Country(case_input.get("country", "ES"))
    application = ApplicationPayload(
        application_id="eval",
        source="eval",
        full_name=case_input.get("name"),
        country=country,
        city=case_input.get("city"),
    )
    return Candidate(
        application=application,
        country=country,
        stage=Stage(case_input.get("stage", Stage.WELCOME)),
        language=Language(case_input.get("language", Language.ES)),
        sentiment=Sentiment(case_input.get("sentiment", Sentiment.NEUTRAL)),
        data=ScreeningData.model_validate(case_input.get("data", {})),
        human_requested=case_input.get("human_requested", False),
        messages=[message(turn["role"], turn["text"]) for turn in case_input.get("history", [])],
    )


def message(role: str, text: str) -> Message:
    return Message(role=MessageRole(role), text=text, channel=ChannelName.WHATSAPP)


def total_usage(usage: list[LLMUsage]) -> LLMUsage | None:
    if not usage:
        return None
    return LLMUsage(
        model=usage[0].model,
        input_tokens=sum(u.input_tokens for u in usage),
        output_tokens=sum(u.output_tokens for u in usage),
        latency_ms=sum(u.latency_ms for u in usage),
        cost_usd=sum(u.cost_usd for u in usage),
    )
