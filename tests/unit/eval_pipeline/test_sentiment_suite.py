"""Suite `sentiment` (014) con un `FakeLLMClient`: sin LLM."""

from __future__ import annotations

from collections import Counter

from evals.dataset import load_suite
from evals.suites import SUITES
from evals.targets import sentiment
from screening.adapters.fake_llm import FakeLLMClient
from screening.domain import Sentiment


def test_sentiment_suite_is_registered_and_covers_every_label() -> None:
    assert "sentiment" in SUITES
    cases = load_suite("sentiment")

    assert len(cases) >= 20
    assert all(case.input["messages"] for case in cases)
    labels = Counter(case.metadata["tags"][1] for case in cases)
    assert set(labels) == {s.value for s in Sentiment}
    assert {case.metadata["tags"][0] for case in cases} == {"es", "mx", "en"}


def test_sentiment_target_returns_label_and_usage(app_config) -> None:
    llm = FakeLLMClient([{"label": "rushed", "confidence": 0.8}])
    case = next(c for c in load_suite("sentiment") if c.id == "se-09-mx-manejando")

    output = sentiment.make_target(llm, app_config)(case)

    assert output.answer == "Etiqueta: rushed (confianza 0.80)"
    assert output.usage.model == app_config.llm.classifier_model
    assert llm.calls[0].messages[0].content.endswith("- ando manejando, rápido porfa")


def test_sentiment_target_falls_back_to_neutral(app_config) -> None:
    llm = FakeLLMClient([{"label": "frustrated", "confidence": 0.1}])
    case = load_suite("sentiment")[0]

    output = sentiment.make_target(llm, app_config)(case)

    assert output.answer.startswith("Etiqueta: neutral")
