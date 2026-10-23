"""Base de conocimiento (spec 010): FAQ de Grupo Sazón indexada con embeddings."""

from screening.knowledge.base import DEFAULT_MIN_SCORE, EmbeddingKnowledgeBase
from screening.knowledge.embeddings import Embedder, EmbeddingResult, OpenAIEmbedder
from screening.knowledge.index import KnowledgeIndex, KnowledgeIndexError, load_index
from screening.knowledge.store import InMemoryVectorStore, VectorStore

__all__ = [
    "DEFAULT_MIN_SCORE",
    "Embedder",
    "EmbeddingKnowledgeBase",
    "EmbeddingResult",
    "InMemoryVectorStore",
    "KnowledgeIndex",
    "KnowledgeIndexError",
    "OpenAIEmbedder",
    "VectorStore",
    "load_index",
]
