from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Literal

import httpx
import openai
import pytest
from pydantic import BaseModel

from screening.adapters import openai_llm
from screening.adapters.llm_pricing import ModelPrice, PricingTable
from screening.adapters.openai_llm import OpenAILLMClient, strict_json_schema
from screening.ports import ChatMessage, LLMError

MODEL = "gpt-5-mini"
REQUEST = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


class Address(BaseModel):
    city: str
    zone: str | None = None


class Extraction(BaseModel):
    name: str
    age: int | None = None
    vehicle: Literal["moto", "bike"]
    addresses: list[Address] = []


def completion(
    content: str | None,
    *,
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    cached_tokens: int = 0,
    finish_reason: str = "stop",
    refusal: str | None = None,
) -> Any:
    return SimpleNamespace(
        model=f"{MODEL}-2025-08-07",
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(content=content, refusal=refusal),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            prompt_tokens_details=SimpleNamespace(cached_tokens=cached_tokens),
        ),
    )


def status_error(cls: type[openai.APIStatusError], status: int) -> openai.APIStatusError:
    response = httpx.Response(status, request=REQUEST)
    return cls(f"error {status}", response=response, body=None)


class FakeOpenAI:
    """Imita `client.chat.completions.create` devolviendo respuestas guionadas."""

    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class Timer:
    """Cada intento dura 250 ms."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 0.125
        return self.now


@pytest.fixture
def pricing() -> PricingTable:
    return PricingTable(
        models={
            MODEL: ModelPrice(
                input_per_million=0.25, cached_input_per_million=0.025, output_per_million=2.0
            )
        }
    )


@pytest.fixture
def sleeps() -> list[float]:
    return []


def make_client(
    pricing: PricingTable, sleeps: list[float], *outcomes: Any, max_retries: int = 2
) -> tuple[OpenAILLMClient, FakeOpenAI]:
    fake = FakeOpenAI(*outcomes)
    client = OpenAILLMClient(
        pricing,
        client=fake,
        max_retries=max_retries,
        backoff_seconds=0.5,
        tracing=False,
        sleep=sleeps.append,
        timer=Timer(),
    )
    return client, fake


MESSAGES = [ChatMessage("user", "Hola"), ChatMessage("assistant", "¿Tienes moto?")]


# --- Texto ------------------------------------------------------------------------


def test_generate_text_sends_system_and_messages(pricing, sleeps) -> None:
    client, fake = make_client(pricing, sleeps, completion("Sí, tengo moto"))

    result = client.generate_text(system="Eres Sofía", messages=MESSAGES, model=MODEL)

    assert result.text == "Sí, tengo moto"
    request = fake.requests[0]
    assert request["model"] == MODEL
    assert request["messages"] == [
        {"role": "system", "content": "Eres Sofía"},
        {"role": "user", "content": "Hola"},
        {"role": "assistant", "content": "¿Tienes moto?"},
    ]
    assert "response_format" not in request


def test_temperature_is_omitted_when_none(pricing, sleeps) -> None:
    client, fake = make_client(pricing, sleeps, completion("ok"))

    client.generate_text(system="s", messages=MESSAGES, model=MODEL)

    assert "temperature" not in fake.requests[0]


def test_temperature_is_sent_when_set(pricing, sleeps) -> None:
    client, fake = make_client(pricing, sleeps, completion("ok"))

    client.generate_text(system="s", messages=MESSAGES, model=MODEL, temperature=0.2)

    assert fake.requests[0]["temperature"] == 0.2


def test_usage_has_tokens_latency_and_cost(pricing, sleeps) -> None:
    client, _ = make_client(
        pricing,
        sleeps,
        completion("ok", prompt_tokens=1_000_000, completion_tokens=1_000_000, cached_tokens=0),
    )

    usage = client.generate_text(system="s", messages=MESSAGES, model=MODEL).usage

    assert usage.model == MODEL
    assert usage.input_tokens == 1_000_000
    assert usage.output_tokens == 1_000_000
    assert usage.latency_ms == pytest.approx(125.0)
    assert usage.cost_usd == pytest.approx(2.25)


def test_cached_tokens_are_priced_as_cached(pricing, sleeps) -> None:
    client, _ = make_client(
        pricing,
        sleeps,
        completion("ok", prompt_tokens=1_000_000, completion_tokens=0, cached_tokens=1_000_000),
    )

    usage = client.generate_text(system="s", messages=MESSAGES, model=MODEL).usage

    assert usage.cost_usd == pytest.approx(0.025)


def test_empty_text_is_retried_once_then_fails(pricing, sleeps) -> None:
    client, fake = make_client(pricing, sleeps, completion(""), completion(None))

    with pytest.raises(LLMError):
        client.generate_text(system="s", messages=MESSAGES, model=MODEL)

    assert len(fake.requests) == 2


# --- Reintentos -------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        status_error(openai.RateLimitError, 429),
        status_error(openai.InternalServerError, 503),
        status_error(openai.APIStatusError, 409),
        openai.APIConnectionError(request=REQUEST),
        openai.APITimeoutError(request=REQUEST),
    ],
)
def test_transient_error_is_retried_with_backoff(pricing, sleeps, error) -> None:
    client, fake = make_client(pricing, sleeps, error, completion("ok"))

    result = client.generate_text(system="s", messages=MESSAGES, model=MODEL)

    assert result.text == "ok"
    assert len(fake.requests) == 2
    assert sleeps == [0.5]


def test_backoff_is_exponential_and_gives_up(pricing, sleeps) -> None:
    errors = [status_error(openai.RateLimitError, 429) for _ in range(4)]
    client, fake = make_client(pricing, sleeps, *errors, max_retries=3)

    with pytest.raises(LLMError):
        client.generate_text(system="s", messages=MESSAGES, model=MODEL)

    assert len(fake.requests) == 4
    assert sleeps == [0.5, 1.0, 2.0]


@pytest.mark.parametrize(
    "error",
    [
        status_error(openai.BadRequestError, 400),
        status_error(openai.AuthenticationError, 401),
        status_error(openai.NotFoundError, 404),
    ],
)
def test_non_transient_error_fails_without_retry(pricing, sleeps, error) -> None:
    client, fake = make_client(pricing, sleeps, error)

    with pytest.raises(LLMError):
        client.generate_text(system="s", messages=MESSAGES, model=MODEL)

    assert len(fake.requests) == 1
    assert sleeps == []


# --- Salida estructurada ----------------------------------------------------------


def test_strict_schema_requires_all_fields_and_forbids_extra() -> None:
    schema = strict_json_schema(Extraction)

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"name", "age", "vehicle", "addresses"}
    address = schema["$defs"]["Address"]
    assert address["additionalProperties"] is False
    assert set(address["required"]) == {"city", "zone"}
    assert "default" not in json.dumps(schema)


def test_generate_structured_sends_strict_response_format(pricing, sleeps) -> None:
    payload = {"name": "Lucía", "age": 25, "vehicle": "moto", "addresses": []}
    client, fake = make_client(pricing, sleeps, completion(json.dumps(payload)))

    result = client.generate_structured(
        system="Extrae", messages=MESSAGES, schema=Extraction, model=MODEL
    )

    assert result.output == Extraction(name="Lucía", age=25, vehicle="moto")
    response_format = fake.requests[0]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == "Extraction"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["schema"] == strict_json_schema(Extraction)


def test_invalid_output_is_retried_once_and_usage_adds_up(pricing, sleeps) -> None:
    valid = json.dumps({"name": "Lucía", "age": None, "vehicle": "bike", "addresses": []})
    client, fake = make_client(
        pricing,
        sleeps,
        completion(
            '{"name": "Lucía", "vehicle": "coche"}', prompt_tokens=100, completion_tokens=10
        ),
        completion(valid, prompt_tokens=100, completion_tokens=20),
    )

    result = client.generate_structured(
        system="s", messages=MESSAGES, schema=Extraction, model=MODEL
    )

    assert result.output.vehicle == "bike"
    assert len(fake.requests) == 2
    assert result.usage.input_tokens == 200
    assert result.usage.output_tokens == 30
    assert result.usage.latency_ms == pytest.approx(250.0)
    assert sleeps == []


@pytest.mark.parametrize(
    "bad",
    [
        completion("no es json"),
        completion(None, refusal="No puedo ayudar con eso"),
        completion('{"name": "Lu', finish_reason="length"),
    ],
)
def test_two_invalid_outputs_raise_llm_error(pricing, sleeps, bad) -> None:
    client, fake = make_client(pricing, sleeps, bad, bad)

    with pytest.raises(LLMError):
        client.generate_structured(system="s", messages=MESSAGES, schema=Extraction, model=MODEL)

    assert len(fake.requests) == 2


def test_transient_error_after_invalid_output_still_retries(pricing, sleeps) -> None:
    valid = json.dumps({"name": "Ana", "age": 30, "vehicle": "moto", "addresses": []})
    client, fake = make_client(
        pricing,
        sleeps,
        completion("no es json"),
        status_error(openai.RateLimitError, 429),
        completion(valid),
    )

    result = client.generate_structured(
        system="s", messages=MESSAGES, schema=Extraction, model=MODEL
    )

    assert result.output.name == "Ana"
    assert len(fake.requests) == 3


# --- Trazas -----------------------------------------------------------------------


def test_tracing_disabled_does_not_wrap(pricing, monkeypatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    fake = FakeOpenAI()

    client = OpenAILLMClient(pricing, client=fake)

    assert client.sdk is fake


def test_tracing_enabled_wraps_with_langsmith(pricing, monkeypatch) -> None:
    import langsmith.wrappers

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    wrapped = object()
    monkeypatch.setattr(langsmith.wrappers, "wrap_openai", lambda c: wrapped)

    client = OpenAILLMClient(pricing, client=FakeOpenAI())

    assert client.sdk is wrapped


def test_tracing_falls_back_when_langsmith_fails(pricing, monkeypatch, caplog) -> None:
    def broken(_: Any) -> Any:
        raise ImportError("sin langsmith")

    monkeypatch.setattr(openai_llm, "_wrap_with_langsmith", broken)
    fake = FakeOpenAI()

    client = OpenAILLMClient(pricing, client=fake, tracing=True)

    assert client.sdk is fake
    assert "LangSmith" in caplog.text
