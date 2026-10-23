"""Objetivo de la suite `extraction`: mensajes del candidato + etapa → campos extraídos.

`input`: `today`, `country`, `stage`, `messages` y opcionalmente `name`, `language`, `data`,
`history` (`[{role, text}]`) y `expected_fields`. La respuesta es el JSON de lo extraído.
"""

from __future__ import annotations

import json

from evals.models import EvalCase, Target, TargetOutput
from evals.targets.common import (
    AgentFactory,
    adk_agent_factory,
    candidate_from_input,
    clock_for,
    message,
    total_usage,
)
from screening.adapters.fake_knowledge import FakeKnowledgeBase
from screening.agent.extraction import CONFIRMATION_FIELD
from screening.config import AppConfig
from screening.ports import ExtractionOutcome, LLMClient


def make_target(
    llm: LLMClient, config: AppConfig, agent_factory: AgentFactory = adk_agent_factory
) -> Target:
    """`llm` no se usa: el agente llama a OpenAI a través de ADK."""

    def target(case: EvalCase) -> TargetOutput:
        candidate = candidate_from_input(case.input)
        new_messages = [message("candidate", text) for text in case.input["messages"]]
        candidate.messages.extend(new_messages)
        agent = agent_factory(config, clock_for(case.input), FakeKnowledgeBase())
        outcome = agent.extract(candidate, new_messages, case.input.get("expected_fields", []))
        return TargetOutput(answer=render(outcome), usage=total_usage(outcome.usage))

    return target


def render(outcome: ExtractionOutcome) -> str:
    """Lo que ve el juez: solo lo extraído y las señales, sin el uso."""
    return json.dumps(
        {
            "updates": outcome.updates.model_dump(
                mode="json", exclude_none=True, exclude={"needs_review"}
            ),
            "invalid_fields": outcome.invalid_fields,
            "language": outcome.language.value,
            "sentiment": outcome.sentiment.value,
            "opt_out": outcome.opt_out,
            "human_requested": outcome.human_requested,
            "question": outcome.question,
            "summary_confirmed": CONFIRMATION_FIELD in outcome.provided_fields,
        },
        ensure_ascii=False,
    )
