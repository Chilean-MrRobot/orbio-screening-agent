"""Objetivo de la suite `rag` con fakes: recupera, redacta y suma el uso."""

from __future__ import annotations

import pytest

from evals.models import EvalCase
from evals.targets.rag import NOT_FOUND_ANSWER, RagAnswerer
from screening.adapters.fake_llm import FakeLLMClient, ScriptedResponse
from screening.knowledge.base import EmbeddingKnowledgeBase
from screening.knowledge.index import IndexedChunk
from screening.knowledge.store import InMemoryVectorStore
from screening.ports import LLMUsage
from tests.unit.knowledge.conftest import FakeEmbedder

CHUNK = IndexedChunk(
    id="pago#propinas",
    source="pago.md · ¿Las propinas son para el repartidor?",
    text="¿Las propinas son para el repartidor?\nSí, íntegras.",
    embedding=[1.0, 0.0],
)


@pytest.fixture
def kb() -> EmbeddingKnowledgeBase:
    embedder = FakeEmbedder(vectors={"¿Las propinas?": [1.0, 0.0], "¿Menú vegano?": [0.0, 1.0]})
    return EmbeddingKnowledgeBase(InMemoryVectorStore([CHUNK]), embedder, min_score=0.5)


def _case(question: str) -> EvalCase:
    return EvalCase(id="c", input={"question": question}, expected="")


def test_answers_with_the_retrieved_snippets(kb: EmbeddingKnowledgeBase) -> None:
    usage = LLMUsage(model="chat", input_tokens=100, output_tokens=20, latency_ms=5, cost_usd=0.01)
    llm = FakeLLMClient([ScriptedResponse("Sí, son tuyas.", usage)])
    answerer = RagAnswerer(kb, llm, model="chat", system="Responde con la FAQ.")

    output = answerer(_case("¿Las propinas?"))

    assert output.answer == "Sí, son tuyas."
    call = llm.calls[0]
    assert call.system == "Responde con la FAQ."
    assert call.model == "chat"
    assert "Sí, íntegras." in call.messages[0].content
    assert "¿Las propinas?" in call.messages[0].content
    assert output.usage is not None
    assert output.usage.cost_usd == pytest.approx(0.011)  # redacción + embedding de la consulta
    assert output.usage.input_tokens == 102


def test_without_snippets_it_does_not_call_the_llm(kb: EmbeddingKnowledgeBase) -> None:
    llm = FakeLLMClient()
    answerer = RagAnswerer(kb, llm, model="chat", system="s")

    output = answerer(_case("¿Menú vegano?"))

    assert output.answer == NOT_FOUND_ANSWER
    assert llm.calls == []
    assert output.usage is not None and output.usage.cost_usd == pytest.approx(0.001)
