"""`LLMClient` sobre la API de Chat Completions de OpenAI, con trazas opcionales en LangSmith."""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Callable
from typing import Any

import openai
from pydantic import BaseModel, ValidationError

from screening.adapters.llm_pricing import PricingTable, load_pricing
from screening.ports import ChatMessage, LLMError, LLMUsage, StructuredResult, TextResult

logger = logging.getLogger(__name__)

_TRANSIENT_STATUS = {408, 409, 429}
_OUTPUT_ATTEMPTS = 2  # una salida inválida se reintenta una sola vez
_DEFAULT_TIMEOUT_SECONDS = 60.0


class _InvalidOutput(Exception):
    """La respuesta llegó pero no sirve (vacía, cortada o rechazada)."""


class OpenAILLMClient:
    """Implementa `LLMClient`. Los reintentos son propios: el SDK se crea con `max_retries=0`."""

    def __init__(
        self,
        pricing: PricingTable,
        *,
        client: Any | None = None,
        max_retries: int = 2,
        backoff_seconds: float = 0.5,
        tracing: bool | None = None,
        sleep: Callable[[float], None] = time.sleep,
        timer: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.pricing = pricing
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self._sleep = sleep
        self._timer = timer

        sdk = client if client is not None else _default_sdk()
        enabled = _tracing_from_env() if tracing is None else tracing
        if enabled:
            try:
                sdk = _wrap_with_langsmith(sdk)
            except Exception as e:
                logger.warning("no se pudo activar LangSmith, se sigue sin trazas: %s", e)
        self.sdk = sdk

    @classmethod
    def from_env(cls, **kwargs: Any) -> OpenAILLMClient:
        """Tabla de precios por defecto y `OPENAI_API_KEY` del entorno."""
        return cls(load_pricing(), **kwargs)

    def generate_text(
        self,
        *,
        system: str,
        messages: list[ChatMessage],
        model: str,
        temperature: float | None = None,
    ) -> TextResult:
        request = _request(system, messages, model, temperature)
        text, usage = self._complete(request, model, parse=lambda content: content)
        return TextResult(text=text, usage=usage)

    def generate_structured[T: BaseModel](
        self,
        *,
        system: str,
        messages: list[ChatMessage],
        schema: type[T],
        model: str,
        temperature: float | None = None,
    ) -> StructuredResult[T]:
        request = _request(system, messages, model, temperature)
        request["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": re.sub(r"[^a-zA-Z0-9_-]", "_", schema.__name__),
                "schema": strict_json_schema(schema),
                "strict": True,
            },
        }
        output, usage = self._complete(request, model, parse=schema.model_validate_json)
        return StructuredResult(output=output, usage=usage)

    # --- Internos -----------------------------------------------------------------

    def _complete[R](
        self, request: dict[str, Any], model: str, parse: Callable[[str], R]
    ) -> tuple[R, LLMUsage]:
        """Llama y valida; una salida inválida se reintenta una vez. El uso suma los intentos."""
        attempts: list[LLMUsage] = []
        last_error: Exception | None = None
        for _ in range(_OUTPUT_ATTEMPTS):
            response, usage = self._call(request, model)
            attempts.append(usage)
            try:
                return parse(_content(response)), _sum_usage(model, attempts)
            except (_InvalidOutput, ValidationError) as e:
                last_error = e
                logger.warning("salida inválida de %s: %s", model, e)
        raise LLMError(f"salida inválida de {model} tras {_OUTPUT_ATTEMPTS} intentos: {last_error}")

    def _call(self, request: dict[str, Any], model: str) -> tuple[Any, LLMUsage]:
        """Una llamada con reintentos y backoff exponencial ante errores transitorios."""
        for attempt in range(self.max_retries + 1):
            start = self._timer()
            try:
                response = self.sdk.chat.completions.create(**request)
            except openai.APIError as e:
                if not _is_transient(e) or attempt == self.max_retries:
                    raise LLMError(f"OpenAI falló con {model}: {e}") from e
                delay = self.backoff_seconds * 2**attempt
                logger.warning("error transitorio de OpenAI (%s), reintento en %.1fs", e, delay)
                self._sleep(delay)
                continue
            latency_ms = (self._timer() - start) * 1000
            return response, self._usage(model, response, latency_ms)
        raise AssertionError("inalcanzable")

    def _usage(self, model: str, response: Any, latency_ms: float) -> LLMUsage:
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
        details = getattr(usage, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", 0) or 0
        return LLMUsage(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            cost_usd=self.pricing.cost(model, input_tokens, output_tokens, cached),
        )


def strict_json_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """JSON schema para Structured Outputs en modo estricto.

    Todos los objetos cierran `additionalProperties` y declaran todas sus propiedades
    como requeridas (los opcionales se expresan como `anyOf` con `null`); se quitan los
    `default` y los `$ref` con hermanos se expanden, porque el modo estricto no los acepta.
    """
    root = schema.model_json_schema()
    return _strict(root, root.get("$defs", {}))


def _strict(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, list):
        return [_strict(item, defs) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node and len(node) > 1:
        ref = node["$ref"].removeprefix("#/$defs/")
        node = {**defs[ref], **{k: v for k, v in node.items() if k != "$ref"}}
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key == "default":
            continue
        if key in ("properties", "$defs"):
            out[key] = {name: _strict(sub, defs) for name, sub in value.items()}
        else:
            out[key] = _strict(value, defs)
    if out.get("type") == "object":
        out["additionalProperties"] = False
        out["required"] = list(out.get("properties", {}))
    return out


def _request(
    system: str, messages: list[ChatMessage], model: str, temperature: float | None
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system}]
        + [{"role": m.role, "content": m.content} for m in messages],
    }
    if temperature is not None:  # los modelos de razonamiento rechazan `temperature`
        request["temperature"] = temperature
    return request


def _content(response: Any) -> str:
    choice = response.choices[0]
    message = choice.message
    if getattr(message, "refusal", None):
        raise _InvalidOutput(f"el modelo se negó a responder: {message.refusal}")
    if choice.finish_reason == "length":
        raise _InvalidOutput("la respuesta se cortó por longitud")
    if not message.content:
        raise _InvalidOutput("respuesta vacía")
    return message.content


def _sum_usage(model: str, attempts: list[LLMUsage]) -> LLMUsage:
    return LLMUsage(
        model=model,
        input_tokens=sum(u.input_tokens for u in attempts),
        output_tokens=sum(u.output_tokens for u in attempts),
        latency_ms=sum(u.latency_ms for u in attempts),
        cost_usd=sum(u.cost_usd for u in attempts),
    )


def _is_transient(error: openai.APIError) -> bool:
    if isinstance(error, openai.APIConnectionError):  # incluye APITimeoutError
        return True
    if isinstance(error, openai.APIStatusError):
        return error.status_code in _TRANSIENT_STATUS or error.status_code >= 500
    return False


def _tracing_from_env() -> bool:
    return os.environ.get("LANGSMITH_TRACING", "").strip().lower() == "true"


def _wrap_with_langsmith(sdk: Any) -> Any:
    from langsmith.wrappers import wrap_openai  # solo se importa si las trazas están activas

    return wrap_openai(sdk)


def _default_sdk() -> Any:
    return openai.OpenAI(max_retries=0, timeout=_DEFAULT_TIMEOUT_SECONDS)
