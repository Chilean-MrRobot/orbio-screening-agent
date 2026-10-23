"""Objetivo de la suite `reply`: `ReplyRequest` (+ FAQ del caso) → mensaje de Sofía.

`input`: `today`, `country`, `stage`, `language`, `name` y opcionalmente `sentiment`, `data`,
`history`, `ask_fields`, `retry_fields`, `candidate_question`, `closing_summary`,
`human_requested` y `faq` (`[[fuente, texto]]`). No se llama `question` a propósito: el juez
mostraría solo esa clave en vez de la petición completa.
"""

from __future__ import annotations

from evals.models import EvalCase, Target, TargetOutput
from evals.targets.common import (
    AgentFactory,
    adk_agent_factory,
    candidate_from_input,
    clock_for,
    total_usage,
)
from screening.adapters.fake_knowledge import FakeKnowledgeBase
from screening.config import AppConfig
from screening.ports import LLMClient, ReplyRequest


def make_target(
    llm: LLMClient, config: AppConfig, agent_factory: AgentFactory = adk_agent_factory
) -> Target:
    """`llm` no se usa: el agente llama a OpenAI a través de ADK."""

    def target(case: EvalCase) -> TargetOutput:
        candidate = candidate_from_input(case.input)
        request = ReplyRequest(
            candidate=candidate,
            stage=candidate.stage,
            ask_fields=case.input.get("ask_fields", []),
            retry_fields=case.input.get("retry_fields", []),
            question=case.input.get("candidate_question"),
            closing_summary=case.input.get("closing_summary", False),
        )
        knowledge = FakeKnowledgeBase(tuple(item) for item in case.input.get("faq", []))
        agent = agent_factory(config, clock_for(case.input), knowledge)
        outcome = agent.reply(request)
        return TargetOutput(answer=outcome.text, usage=total_usage(outcome.usage))

    return target
