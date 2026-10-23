from __future__ import annotations

import pytest

from screening.knowledge.base import DEFAULT_MIN_SCORE, EmbeddingKnowledgeBase
from screening.knowledge.index import IndexedChunk, KnowledgeIndex, KnowledgeIndexError
from screening.knowledge.store import InMemoryVectorStore, cosine
from screening.ports import KnowledgeBase, KnowledgeSnippet
from tests.unit.knowledge.conftest import FakeEmbedder


def _chunk(name: str, embedding: list[float]) -> IndexedChunk:
    return IndexedChunk(id=name, source=f"{name}.md", text=f"texto de {name}", embedding=embedding)


CHUNKS = [
    _chunk("pago", [1.0, 0.0, 0.0]),
    _chunk("turnos", [0.0, 1.0, 0.0]),
    _chunk("mixto", [0.8, 0.6, 0.0]),
]
QUERIES = {
    "¿cuánto pagan?": [1.0, 0.0, 0.0],  # pago 1.0 · mixto 0.8 · turnos 0
    "¿pagan los turnos?": [1.0, 1.0, 0.0],  # mixto 0.99 · pago 0.71 · turnos 0.71
    "¿tienen menú vegano?": [0.1, 0.0, 1.0],  # pago 0.1 · mixto 0.08 · turnos 0
}


@pytest.fixture
def query_embedder() -> FakeEmbedder:
    return FakeEmbedder(vectors=QUERIES)


@pytest.fixture
def kb(query_embedder: FakeEmbedder) -> EmbeddingKnowledgeBase:
    return EmbeddingKnowledgeBase(InMemoryVectorStore(CHUNKS), query_embedder, min_score=0.5)


def test_implements_the_port(kb: EmbeddingKnowledgeBase) -> None:
    port: KnowledgeBase = kb

    assert isinstance(port.search("¿cuánto pagan?")[0], KnowledgeSnippet)


def test_results_are_ranked_by_cosine_similarity(kb: EmbeddingKnowledgeBase) -> None:
    results = kb.search("¿pagan los turnos?")

    assert [s.source for s in results] == ["mixto.md", "pago.md", "turnos.md"]
    assert results[0].text == "texto de mixto"
    assert results[0].score == pytest.approx(0.98995, abs=1e-4)
    assert results[0].score >= results[1].score >= results[2].score


def test_results_below_the_threshold_are_dropped(kb: EmbeddingKnowledgeBase) -> None:
    assert [s.source for s in kb.search("¿cuánto pagan?")] == ["pago.md", "mixto.md"]


def test_unrelated_question_returns_nothing(kb: EmbeddingKnowledgeBase) -> None:
    assert kb.search("¿tienen menú vegano?") == []


def test_limit_caps_the_results(kb: EmbeddingKnowledgeBase) -> None:
    assert [s.source for s in kb.search("¿pagan los turnos?", limit=1)] == ["mixto.md"]


@pytest.mark.parametrize("query, limit", [("", 3), ("   ", 3), ("¿cuánto pagan?", 0)])
def test_trivial_searches_do_not_call_the_api(
    kb: EmbeddingKnowledgeBase, query_embedder: FakeEmbedder, query: str, limit: int
) -> None:
    assert kb.search(query, limit=limit) == []
    assert query_embedder.calls == []
    assert kb.last_usage is None


def test_empty_index_returns_nothing_without_calls(query_embedder: FakeEmbedder) -> None:
    kb = EmbeddingKnowledgeBase(InMemoryVectorStore([]), query_embedder)

    assert kb.search("¿cuánto pagan?") == []
    assert query_embedder.calls == []


def test_last_usage_is_the_query_embedding(
    kb: EmbeddingKnowledgeBase, query_embedder: FakeEmbedder
) -> None:
    kb.search("¿cuánto pagan?")

    assert query_embedder.calls == [["¿cuánto pagan?"]]
    assert kb.last_usage is not None
    assert kb.last_usage.cost_usd == pytest.approx(0.001)


def test_from_index_uses_the_default_threshold(query_embedder: FakeEmbedder) -> None:
    index = KnowledgeIndex(model=query_embedder.model, corpus_hash="h", chunks=CHUNKS)

    kb = EmbeddingKnowledgeBase.from_index(index, query_embedder)

    assert kb.min_score == DEFAULT_MIN_SCORE
    assert kb.search("¿cuánto pagan?")[0].source == "pago.md"


def test_from_index_rejects_another_embedding_model(query_embedder: FakeEmbedder) -> None:
    index = KnowledgeIndex(model="otro-modelo", corpus_hash="h", chunks=CHUNKS)

    with pytest.raises(KnowledgeIndexError, match="otro-modelo"):
        EmbeddingKnowledgeBase.from_index(index, query_embedder)


def test_cosine() -> None:
    assert cosine([1.0, 0.0], [2.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 3.0]) == pytest.approx(0.0)
    assert cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_store_query_respects_limit() -> None:
    store = InMemoryVectorStore(CHUNKS)

    assert len(store) == 3
    assert [c.id for c, _ in store.query([1.0, 0.0, 0.0], limit=2)] == ["pago", "mixto"]


def test_bag_of_words_end_to_end(embedder: FakeEmbedder) -> None:
    """Con el embedder de palabras, la pregunta encuentra el fragmento que comparte términos."""
    texts = {
        "pago": "¿Cuánto se paga en México? De 70 a 95 pesos por hora.",
        "turnos": "¿Qué turnos hay? Mañana, tarde y noche.",
    }
    chunks = [
        IndexedChunk(id=k, source=k, text=v, embedding=embedder.embed([v]).vectors[0])
        for k, v in texts.items()
    ]
    kb = EmbeddingKnowledgeBase(InMemoryVectorStore(chunks), embedder, min_score=0.3)

    assert [s.source for s in kb.search("¿cuánto se paga por hora en México?")] == ["pago"]
