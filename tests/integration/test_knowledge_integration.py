"""Embeddings reales de OpenAI sobre la FAQ del repo. Se saltan sin `OPENAI_API_KEY`."""

from __future__ import annotations

import os

import pytest

from screening.knowledge.base import EmbeddingKnowledgeBase
from screening.knowledge.chunking import load_chunks
from screening.knowledge.embeddings import OpenAIEmbedder
from screening.knowledge.index import build_index

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="falta OPENAI_API_KEY"),
]


@pytest.fixture(scope="module")
def kb(app_config) -> EmbeddingKnowledgeBase:
    embedder = OpenAIEmbedder.from_env(app_config.llm.embedding_model)
    index = build_index(load_chunks(), embedder).index  # en memoria: unos 600 tokens
    return EmbeddingKnowledgeBase.from_index(index, embedder)


def test_finds_the_mexico_pay_chunk(kb: EmbeddingKnowledgeBase) -> None:
    results = kb.search("¿Cuánto me pagan por hora si reparto en Guadalajara?")

    assert results
    assert results[0].source.startswith("pago.md · ¿Cuánto se paga en México?")
    assert kb.last_usage is not None and kb.last_usage.cost_usd > 0


def test_unrelated_question_returns_nothing(kb: EmbeddingKnowledgeBase) -> None:
    assert kb.search("¿Cuál es la capital de Australia?") == []
