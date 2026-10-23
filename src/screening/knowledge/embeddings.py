"""Embeddings: interfaz `Embedder` y su implementación con la API de OpenAI."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import openai

from screening.adapters.llm_pricing import PricingTable, load_pricing
from screening.ports import LLMError, LLMUsage

_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_BATCH_SIZE = 100


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]
    usage: LLMUsage


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> EmbeddingResult: ...


class OpenAIEmbedder:
    """`Embedder` sobre `embeddings.create`. El SDK reintenta los errores transitorios."""

    def __init__(
        self,
        model: str,
        pricing: PricingTable,
        *,
        client: Any | None = None,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        timer: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.model = model
        self.pricing = pricing
        self.batch_size = batch_size
        self._timer = timer
        self.sdk = client if client is not None else openai.OpenAI(timeout=_DEFAULT_TIMEOUT_SECONDS)

    @classmethod
    def from_env(cls, model: str, **kwargs: Any) -> OpenAIEmbedder:
        """Tabla de precios por defecto y `OPENAI_API_KEY` del entorno."""
        return cls(model, load_pricing(), **kwargs)

    def embed(self, texts: list[str]) -> EmbeddingResult:
        vectors: list[list[float]] = []
        tokens = 0
        latency_ms = 0.0
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            began = self._timer()
            try:
                response = self.sdk.embeddings.create(model=self.model, input=batch)
            except openai.APIError as e:
                raise LLMError(f"OpenAI falló al calcular embeddings con {self.model}: {e}") from e
            latency_ms += (self._timer() - began) * 1000
            vectors.extend(item.embedding for item in sorted(response.data, key=lambda d: d.index))
            tokens += getattr(response.usage, "prompt_tokens", 0) or 0
        usage = LLMUsage(
            model=self.model,
            input_tokens=tokens,
            output_tokens=0,
            latency_ms=latency_ms,
            cost_usd=self.pricing.cost(self.model, tokens, 0),
        )
        return EmbeddingResult(vectors=vectors, usage=usage)
