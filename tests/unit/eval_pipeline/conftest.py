"""Fixtures del pipeline de evaluación (007)."""

from __future__ import annotations

import pytest

from screening.adapters.fake_llm import FakeLLMClient, LLMCall
from tests.unit.eval_pipeline.helpers import verdict


@pytest.fixture
def judge_llm() -> FakeLLMClient:
    """Juez falso: aprueba si la respuesta del sistema contiene la palabra «sí»."""

    def respond(call: LLMCall):
        answer = call.messages[-1].content.split("<respuesta_sistema>")[-1].lower()
        return verdict("pass", 5) if "sí" in answer else verdict("fail", 1)

    return FakeLLMClient(responder=respond, input_tokens=100, output_tokens=20, cost_usd=0.001)
