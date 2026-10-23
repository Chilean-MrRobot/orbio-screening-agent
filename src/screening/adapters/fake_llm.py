"""`LLMClient` falso con respuestas guionadas, para tests sin red."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from screening.ports import ChatMessage, LLMError, LLMUsage, StructuredResult, TextResult


@dataclass(frozen=True)
class LLMCall:
    method: Literal["text", "structured"]
    system: str
    messages: list[ChatMessage]
    model: str
    temperature: float | None
    schema: type[BaseModel] | None


@dataclass(frozen=True)
class ScriptedResponse:
    """Una respuesta guionada con un uso propio (tokens, latencia, costo)."""

    value: Any
    usage: LLMUsage | None = None


Responder = Callable[[LLMCall], Any]


class FakeLLMClient:
    """Devuelve las respuestas en orden y registra cada llamada en `calls`.

    Cada respuesta puede ser `str`, `dict`, un `BaseModel`, una `Exception` (se lanza) o un
    `ScriptedResponse`. Si se acaba la cola, responde `responder(call)`; sin `responder`, el
    test falla con `AssertionError`. Una salida estructurada que no valida lanza `LLMError`,
    como el cliente real.
    """

    def __init__(
        self,
        responses: Iterable[Any] = (),
        *,
        responder: Responder | None = None,
        input_tokens: int = 10,
        output_tokens: int = 5,
        latency_ms: float = 1.0,
        cost_usd: float = 0.0,
    ) -> None:
        self._queue: deque[Any] = deque(responses)
        self._responder = responder
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self._latency_ms = latency_ms
        self._cost_usd = cost_usd
        self.calls: list[LLMCall] = []

    def queue(self, *responses: Any) -> None:
        self._queue.extend(responses)

    def generate_text(
        self,
        *,
        system: str,
        messages: list[ChatMessage],
        model: str,
        temperature: float | None = None,
    ) -> TextResult:
        call = LLMCall("text", system, list(messages), model, temperature, None)
        value, usage = self._next(call)
        if not isinstance(value, str):
            raise AssertionError(f"FakeLLMClient: generate_text recibió {type(value).__name__}")
        return TextResult(text=value, usage=usage)

    def generate_structured[T: BaseModel](
        self,
        *,
        system: str,
        messages: list[ChatMessage],
        schema: type[T],
        model: str,
        temperature: float | None = None,
    ) -> StructuredResult[T]:
        call = LLMCall("structured", system, list(messages), model, temperature, schema)
        value, usage = self._next(call)
        try:
            if isinstance(value, schema):
                output = value
            elif isinstance(value, BaseModel):
                output = schema.model_validate(value.model_dump())
            elif isinstance(value, str):
                output = schema.model_validate_json(value)
            else:
                output = schema.model_validate(value)
        except ValidationError as e:
            raise LLMError(f"FakeLLMClient: salida que no cumple {schema.__name__}: {e}") from e
        return StructuredResult(output=output, usage=usage)

    def _next(self, call: LLMCall) -> tuple[Any, LLMUsage]:
        self.calls.append(call)
        if self._queue:
            value = self._queue.popleft()
        elif self._responder is not None:
            value = self._responder(call)
        else:
            raise AssertionError("FakeLLMClient: no quedan respuestas guionadas")

        usage = None
        if isinstance(value, ScriptedResponse):
            value, usage = value.value, value.usage
        if isinstance(value, Exception):
            raise value
        return value, usage or LLMUsage(
            model=call.model,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            latency_ms=self._latency_ms,
            cost_usd=self._cost_usd,
        )
