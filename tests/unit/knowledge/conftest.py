"""Embedder falso y determinista para los tests de la base de conocimiento (sin red)."""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections.abc import Mapping

import pytest

from screening.knowledge.embeddings import EmbeddingResult
from screening.ports import LLMUsage

DIMENSIONS = 64


def bag_of_words(text: str) -> list[float]:
    """Cada palabra suma 1 en una posición fija (md5, no `hash()`, que cambia por proceso)."""
    plain = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    vector = [0.0] * DIMENSIONS
    for word in re.findall(r"[a-z0-9]{3,}", plain):
        vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % DIMENSIONS] += 1.0
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else vector


class FakeEmbedder:
    """Bolsa de palabras, o el vector fijado en `vectors` para ese texto. Registra cada lote."""

    def __init__(
        self, model: str = "fake-embedding", vectors: Mapping[str, list[float]] | None = None
    ) -> None:
        self.model = model
        self.vectors = dict(vectors or {})
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> EmbeddingResult:
        self.calls.append(list(texts))
        vectors = [self.vectors.get(t) or bag_of_words(t) for t in texts]
        usage = LLMUsage(
            model=self.model,
            input_tokens=sum(len(t.split()) for t in texts),
            output_tokens=0,
            latency_ms=1.0,
            cost_usd=0.001 * len(texts),
        )
        return EmbeddingResult(vectors=vectors, usage=usage)

    @property
    def embedded_texts(self) -> list[str]:
        return [t for batch in self.calls for t in batch]


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()
