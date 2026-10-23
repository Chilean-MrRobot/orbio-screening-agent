"""Clasificador de emoción con un modelo chico: ajusta el tono de la respuesta (spec 014)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from screening.agent.prompts import DEFAULT_PROMPTS_DIR, load_prompt
from screening.config import AppConfig
from screening.domain import Sentiment
from screening.ports import ChatMessage, LLMClient, LLMUsage

logger = logging.getLogger(__name__)

DEFAULT_PROMPT_PATH = DEFAULT_PROMPTS_DIR / "sentiment.md"


class SentimentResponse(BaseModel):
    """Salida estructurada del clasificador: lo mínimo, para gastar pocos tokens."""

    label: Sentiment
    confidence: float = Field(ge=0, le=1)


@dataclass(frozen=True)
class SentimentResult:
    """`label` es `None` si el clasificador falló o no estaba seguro: se usa la heurística."""

    label: Sentiment | None
    confidence: float = 0.0
    usage: list[LLMUsage] = field(default_factory=list)


class SentimentClassifier:
    """Etiqueta el estado de ánimo con los últimos mensajes del candidato. Nunca lanza."""

    def __init__(
        self,
        llm: LLMClient,
        *,
        model: str,
        prompt_path: Path | str = DEFAULT_PROMPT_PATH,
        min_confidence: float = 0.6,
        message_limit: int = 3,
    ) -> None:
        self.llm = llm
        self.model = model
        self.prompt = load_prompt(Path(prompt_path))
        self.min_confidence = min_confidence
        self.message_limit = message_limit
        self._system = self.prompt.render()

    @classmethod
    def from_config(cls, config: AppConfig, llm: LLMClient, **kwargs: Any) -> SentimentClassifier:
        return cls(llm, model=config.llm.classifier_model, **kwargs)

    @classmethod
    def from_env(cls, config: AppConfig, **kwargs: Any) -> SentimentClassifier:
        """`OpenAILLMClient` con `OPENAI_API_KEY` del entorno."""
        from screening.adapters.openai_llm import OpenAILLMClient

        return cls.from_config(config, OpenAILLMClient.from_env(), **kwargs)

    def classify(self, texts: list[str]) -> SentimentResult:
        recent = [t.strip() for t in texts if t.strip()][-self.message_limit :]
        if not recent:
            return SentimentResult(label=None)
        content = "Mensajes del candidato (el último es el más reciente):\n" + "\n".join(
            f"- {text}" for text in recent
        )
        try:
            result = self.llm.generate_structured(
                system=self._system,
                messages=[ChatMessage("user", content)],
                schema=SentimentResponse,
                model=self.model,
            )
        except Exception as e:  # el tono es un extra: una falla no bloquea el turno
            logger.warning("falló el clasificador de emoción: %s", e)
            return SentimentResult(label=None)

        output = result.output
        if output.confidence < self.min_confidence:
            return SentimentResult(None, output.confidence, [result.usage])
        return SentimentResult(output.label, output.confidence, [result.usage])
