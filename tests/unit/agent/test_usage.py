import pytest
from google.genai import types

from screening.adapters.llm_pricing import PricingTable
from screening.agent.runtime import model_name, usage_from_metadata
from screening.domain import Candidate, Stage
from screening.ports import ReplyRequest
from tests.unit.agent.fakes import tool_call


def test_usage_metadata_becomes_llm_usage_with_cached_cost(pricing: PricingTable) -> None:
    metadata = types.GenerateContentResponseUsageMetadata(
        prompt_token_count=1000,
        candidates_token_count=200,
        cached_content_token_count=400,
        thoughts_token_count=150,  # ya incluido en candidates_token_count
    )

    usage = usage_from_metadata(
        metadata, model="openai/gpt-5-mini", latency_ms=850.0, pricing=pricing
    )

    assert usage.model == "gpt-5-mini"
    assert (usage.input_tokens, usage.output_tokens, usage.latency_ms) == (1000, 200, 850.0)
    assert usage.cost_usd == pytest.approx(pricing.cost("gpt-5-mini", 1000, 200, 400))
    assert usage.cost_usd == pytest.approx((600 * 0.25 + 400 * 0.025 + 200 * 2.0) / 1_000_000)


def test_missing_counts_are_zero(pricing: PricingTable) -> None:
    usage = usage_from_metadata(
        types.GenerateContentResponseUsageMetadata(),
        model="gpt-5-mini",
        latency_ms=1,
        pricing=pricing,
    )

    assert (usage.input_tokens, usage.output_tokens, usage.cost_usd) == (0, 0, 0.0)


@pytest.mark.parametrize(
    ("model", "expected"), [("openai/gpt-5-mini", "gpt-5-mini"), ("gpt-5-nano", "gpt-5-nano")]
)
def test_model_name_drops_provider_prefix(model: str, expected: str) -> None:
    assert model_name(model) == expected


def test_each_model_call_is_one_usage_entry_with_its_latency(
    make_agent, candidate: Candidate
) -> None:
    agent, _, _ = make_agent(reply=[tool_call("search_faq", query="pagos"), "¿Tienes carnet?"])

    outcome = agent.reply(
        ReplyRequest(candidate=candidate, stage=Stage.KNOCKOUT, question="¿Cuándo pagan?")
    )

    assert [u.model for u in outcome.usage] == ["gpt-5-mini", "gpt-5-mini"]
    assert [(u.input_tokens, u.output_tokens) for u in outcome.usage] == [(100, 20), (100, 20)]
    # El Ticker avanza 0,5 s por lectura: cada llamada mide desde el evento anterior.
    assert all(u.latency_ms == 500.0 for u in outcome.usage)
