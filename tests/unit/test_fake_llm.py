from __future__ import annotations

import pytest
from pydantic import BaseModel

from screening.adapters.fake_llm import FakeLLMClient, LLMCall, ScriptedResponse
from screening.ports import ChatMessage, LLMError, LLMUsage

MESSAGES = [ChatMessage("user", "Hola")]


class Answer(BaseModel):
    verdict: str
    score: int


def test_returns_scripted_text_in_order() -> None:
    llm = FakeLLMClient(["uno", "dos"])

    first = llm.generate_text(system="s", messages=MESSAGES, model="m")
    second = llm.generate_text(system="s", messages=MESSAGES, model="m")

    assert (first.text, second.text) == ("uno", "dos")


def test_structured_accepts_dict_model_and_json() -> None:
    llm = FakeLLMClient(
        [
            {"verdict": "pass", "score": 5},
            Answer(verdict="fail", score=1),
            '{"verdict": "pass", "score": 3}',
        ]
    )

    outputs = [
        llm.generate_structured(system="s", messages=MESSAGES, schema=Answer, model="m").output
        for _ in range(3)
    ]

    assert outputs == [
        Answer(verdict="pass", score=5),
        Answer(verdict="fail", score=1),
        Answer(verdict="pass", score=3),
    ]


def test_structured_output_that_does_not_validate_raises_llm_error() -> None:
    llm = FakeLLMClient([{"verdict": "pass"}])

    with pytest.raises(LLMError):
        llm.generate_structured(system="s", messages=MESSAGES, schema=Answer, model="m")


def test_scripted_exception_is_raised() -> None:
    llm = FakeLLMClient([LLMError("caído")])

    with pytest.raises(LLMError, match="caído"):
        llm.generate_text(system="s", messages=MESSAGES, model="m")


def test_records_calls() -> None:
    llm = FakeLLMClient(["ok", {"verdict": "pass", "score": 4}])

    llm.generate_text(system="sys", messages=MESSAGES, model="m1", temperature=0.3)
    llm.generate_structured(system="sys2", messages=MESSAGES, schema=Answer, model="m2")

    assert llm.calls == [
        LLMCall("text", "sys", MESSAGES, "m1", 0.3, None),
        LLMCall("structured", "sys2", MESSAGES, "m2", None, Answer),
    ]


def test_default_usage_uses_called_model() -> None:
    llm = FakeLLMClient(["ok"], input_tokens=7, output_tokens=3, latency_ms=2.0, cost_usd=0.01)

    usage = llm.generate_text(system="s", messages=MESSAGES, model="gpt-x").usage

    assert usage == LLMUsage("gpt-x", 7, 3, 2.0, 0.01)


def test_scripted_response_overrides_usage() -> None:
    custom = LLMUsage("m", 100, 50, 30.0, 0.5)
    llm = FakeLLMClient([ScriptedResponse("ok", usage=custom)])

    assert llm.generate_text(system="s", messages=MESSAGES, model="m").usage == custom


def test_responder_answers_when_queue_is_empty() -> None:
    llm = FakeLLMClient(["guionada"], responder=lambda call: f"eco: {call.messages[-1].content}")

    first = llm.generate_text(system="s", messages=MESSAGES, model="m")
    second = llm.generate_text(system="s", messages=MESSAGES, model="m")

    assert (first.text, second.text) == ("guionada", "eco: Hola")


def test_exhausted_script_fails_loudly() -> None:
    llm = FakeLLMClient()

    with pytest.raises(AssertionError):
        llm.generate_text(system="s", messages=MESSAGES, model="m")


def test_text_call_with_non_text_response_fails_loudly() -> None:
    llm = FakeLLMClient([{"a": 1}])

    with pytest.raises(AssertionError):
        llm.generate_text(system="s", messages=MESSAGES, model="m")
