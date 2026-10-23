"""Almacén de vectores. Único punto que conoce dónde viven los embeddings.

Hoy es una lista en memoria (la FAQ tiene decenas de fragmentos). Para escalar a pgvector o a
Vertex AI Vector Search se implementa otro `VectorStore`; la base de conocimiento no cambia.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol

from screening.knowledge.index import IndexedChunk


class VectorStore(Protocol):
    def __len__(self) -> int: ...

    def query(self, vector: list[float], *, limit: int) -> list[tuple[IndexedChunk, float]]:
        """Los `limit` fragmentos más similares, de mayor a menor score."""
        ...


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    norm = _norm(a) * _norm(b)
    return sum(x * y for x, y in zip(a, b, strict=True)) / norm if norm else 0.0


def _norm(vector: Sequence[float]) -> float:
    return math.sqrt(sum(x * x for x in vector))


class InMemoryVectorStore:
    """Coseno contra todos los fragmentos. Los empates conservan el orden del índice."""

    def __init__(self, chunks: Sequence[IndexedChunk]) -> None:
        self._chunks = list(chunks)

    def __len__(self) -> int:
        return len(self._chunks)

    def query(self, vector: list[float], *, limit: int) -> list[tuple[IndexedChunk, float]]:
        scored = [(chunk, cosine(vector, chunk.embedding)) for chunk in self._chunks]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[: max(limit, 0)]
