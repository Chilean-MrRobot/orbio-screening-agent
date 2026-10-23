"""Llamadas reales a OpenAI. Se saltan sin `OPENAI_API_KEY`."""

from __future__ import annotations

import os
from typing import Literal

import pytest
from pydantic import BaseModel

from screening.adapters.openai_llm import OpenAILLMClient
from screening.ports import ChatMessage

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="falta OPENAI_API_KEY"),
]


class Vehicle(BaseModel):
    vehicle: Literal["moto", "bike", "car", "none"]
    has_license: bool | None


@pytest.fixture(scope="module")
def llm() -> OpenAILLMClient:
    return OpenAILLMClient.from_env()


def test_generate_text(llm: OpenAILLMClient, app_config) -> None:
    result = llm.generate_text(
        system="Responde con una sola palabra.",
        messages=[ChatMessage("user", "¿De qué color es el cielo despejado?")],
        model=app_config.llm.classifier_model,
    )

    assert result.text
    assert result.usage.input_tokens > 0
    assert result.usage.cost_usd > 0


def test_generate_structured(llm: OpenAILLMClient, app_config) -> None:
    result = llm.generate_structured(
        system="Extrae el vehículo del candidato y si tiene licencia.",
        messages=[ChatMessage("user", "Tengo una moto de 125 y carnet A1")],
        schema=Vehicle,
        model=app_config.llm.classifier_model,
    )

    assert result.output.vehicle == "moto"
    assert result.output.has_license is True
