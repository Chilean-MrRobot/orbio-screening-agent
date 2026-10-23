"""`KnowledgeBase` por embeddings: similitud coseno y umbral mínimo de score."""

from __future__ import annotations

from screening.knowledge.embeddings import Embedder
from screening.knowledge.index import KnowledgeIndex, KnowledgeIndexError
from screening.knowledge.store import InMemoryVectorStore, VectorStore
from screening.ports import KnowledgeSnippet, LLMUsage

# Calibrado para text-embedding-3-small: lo relacionado queda sobre ~0,45 y lo ajeno bajo ~0,30.
DEFAULT_MIN_SCORE = 0.35


class EmbeddingKnowledgeBase:
    """Implementa `KnowledgeBase`. Una lista vacía significa que la respuesta no está en la FAQ."""

    def __init__(
        self, store: VectorStore, embedder: Embedder, *, min_score: float = DEFAULT_MIN_SCORE
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.min_score = min_score
        self.last_usage: LLMUsage | None = None

    @classmethod
    def from_index(
        cls, index: KnowledgeIndex, embedder: Embedder, *, min_score: float = DEFAULT_MIN_SCORE
    ) -> EmbeddingKnowledgeBase:
        if index.model != embedder.model:
            raise KnowledgeIndexError(
                f"el índice se construyó con {index.model} y las consultas usan {embedder.model}: "
                "reconstruirlo con `python -m screening.knowledge.build`"
            )
        return cls(InMemoryVectorStore(index.chunks), embedder, min_score=min_score)

    def search(self, query: str, *, limit: int = 3) -> list[KnowledgeSnippet]:
        self.last_usage = None
        if not query.strip() or limit <= 0 or len(self.store) == 0:
            return []
        result = self.embedder.embed([query])
        self.last_usage = result.usage
        return [
            KnowledgeSnippet(source=chunk.source, text=chunk.text, score=score)
            for chunk, score in self.store.query(result.vectors[0], limit=limit)
            if score >= self.min_score
        ]
