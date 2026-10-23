from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from screening.adapters.llm_pricing import load_pricing
from screening.knowledge.embeddings import OpenAIEmbedder
from screening.ports import LLMError

MODEL = "text-embedding-3-small"


class FakeEmbeddingsAPI:
    def __init__(self, error: Exception | None = None) -> None:
        self.requests: list[dict[str, Any]] = []
        self.error = error

    def create(self, **request: Any) -> SimpleNamespace:
        self.requests.append(request)
        if self.error:
            raise self.error
        texts = request["input"]
        # La API puede devolver los vectores en otro orden: se ordenan por `index`.
        data = [
            SimpleNamespace(index=i, embedding=[float(len(t)), 1.0]) for i, t in enumerate(texts)
        ]
        return SimpleNamespace(
            data=data[::-1], usage=SimpleNamespace(prompt_tokens=10 * len(texts))
        )


def _embedder(api: FakeEmbeddingsAPI, batch_size: int = 100) -> OpenAIEmbedder:
    ticks = iter(range(100))
    return OpenAIEmbedder(
        MODEL,
        load_pricing(),
        client=SimpleNamespace(embeddings=api),
        batch_size=batch_size,
        timer=lambda: next(ticks) / 1000,
    )


def test_embeds_in_order_and_prices_the_tokens() -> None:
    api = FakeEmbeddingsAPI()

    result = _embedder(api).embed(["a", "bb"])

    assert result.vectors == [[1.0, 1.0], [2.0, 1.0]]
    assert api.requests == [{"model": MODEL, "input": ["a", "bb"]}]
    assert result.usage.model == MODEL
    assert result.usage.input_tokens == 20
    assert result.usage.output_tokens == 0
    assert result.usage.cost_usd == pytest.approx(20 * 0.02 / 1_000_000)
    assert result.usage.latency_ms == pytest.approx(1.0)


def test_large_inputs_are_split_in_batches() -> None:
    api = FakeEmbeddingsAPI()

    result = _embedder(api, batch_size=2).embed(["a", "bb", "ccc"])

    assert [r["input"] for r in api.requests] == [["a", "bb"], ["ccc"]]
    assert [v[0] for v in result.vectors] == [1.0, 2.0, 3.0]
    assert result.usage.input_tokens == 30


def test_no_texts_no_call() -> None:
    api = FakeEmbeddingsAPI()

    result = _embedder(api).embed([])

    assert result.vectors == []
    assert api.requests == []


def test_api_errors_become_llm_errors() -> None:
    request = httpx.Request("POST", "https://api.openai.com/v1/embeddings")
    api = FakeEmbeddingsAPI(error=openai.APIConnectionError(request=request))

    with pytest.raises(LLMError, match=MODEL):
        _embedder(api).embed(["a"])
