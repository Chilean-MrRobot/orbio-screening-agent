"""Objetivo de la suite `sentiment` (spec 014): últimos mensajes del candidato → etiqueta.

`input`: `messages` (los últimos mensajes del candidato, el más reciente al final). Llama al
clasificador real con el `LLMClient` del pipeline; sin etiqueta confiable responde `neutral`,
que es lo que vería el agente cuando la heurística de la extracción no detecta nada.
"""

from __future__ import annotations

from evals.models import EvalCase, Target, TargetOutput
from evals.targets.common import total_usage
from screening.agent.sentiment import SentimentClassifier
from screening.config import AppConfig
from screening.domain import Sentiment
from screening.ports import LLMClient


def make_target(llm: LLMClient, config: AppConfig) -> Target:
    classifier = SentimentClassifier.from_config(config, llm)

    def target(case: EvalCase) -> TargetOutput:
        result = classifier.classify([str(text) for text in case.input["messages"]])
        label = result.label or Sentiment.NEUTRAL
        answer = f"Etiqueta: {label.value} (confianza {result.confidence:.2f})"
        return TargetOutput(answer=answer, usage=total_usage(result.usage))

    return target
