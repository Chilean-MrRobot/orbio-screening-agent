from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from screening.adapters.fake_knowledge import FakeKnowledgeBase
from screening.adapters.llm_pricing import PricingTable, load_pricing
from screening.agent import AdkScreeningAgent
from screening.clock import FixedClock
from screening.config import AppConfig
from tests.unit.agent.fakes import ScriptedLlm

FAQ = [
    ("faq/pagos.md", "Los pagos se hacen cada semana por transferencia."),
    ("faq/propinas.md", "Las propinas son íntegras para el repartidor."),
]


@pytest.fixture(scope="session")
def pricing() -> PricingTable:
    return load_pricing()


class Ticker:
    """Timer que avanza 0,5 s en cada lectura: latencias deterministas."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 0.5
        return self.now


@pytest.fixture
def knowledge() -> FakeKnowledgeBase:
    return FakeKnowledgeBase(FAQ)


@pytest.fixture
def make_agent(
    app_config: AppConfig, clock: FixedClock, pricing: PricingTable, knowledge: FakeKnowledgeBase
) -> Callable[..., tuple[AdkScreeningAgent, ScriptedLlm, ScriptedLlm]]:
    """(agente, modelo de extracción, modelo de respuesta) con los guiones dados."""

    def make(
        extraction: list[Any] | None = None, reply: list[Any] | None = None, **kwargs: Any
    ) -> tuple[AdkScreeningAgent, ScriptedLlm, ScriptedLlm]:
        extraction_model = ScriptedLlm(script=list(extraction or []))
        reply_model = ScriptedLlm(script=list(reply or []))
        kwargs.setdefault("tracing", False)
        kwargs.setdefault("timer", Ticker())
        agent = AdkScreeningAgent(
            app_config,
            knowledge,
            clock=clock,
            pricing=pricing,
            extraction_model=extraction_model,
            reply_model=reply_model,
            **kwargs,
        )
        return agent, extraction_model, reply_model

    return make
