"""`BaseLlm` guionado: el agente de ADK corre de verdad, pero el modelo no llama a la red."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.models._capabilities import LlmCapabilities
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import Field


def tool_call(name: str, **args: Any) -> types.Part:
    return types.Part(function_call=types.FunctionCall(name=name, args=args))


def usage(
    prompt: int = 100, output: int = 20, cached: int = 0
) -> types.GenerateContentResponseUsageMetadata:
    return types.GenerateContentResponseUsageMetadata(
        prompt_token_count=prompt, candidates_token_count=output, cached_content_token_count=cached
    )


class ScriptedLlm(BaseLlm):
    """Cada llamada consume una respuesta del guion.

    Una respuesta puede ser `str` (texto), `dict` (se serializa a JSON), `types.Part`
    (por ejemplo, una llamada a tool), `LlmResponse` (tal cual) o una `Exception` (se lanza).
    """

    model: str = "openai/gpt-5-mini"
    script: list[Any] = Field(default_factory=list)
    requests: list[LlmRequest] = Field(default_factory=list)

    @property
    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities(output_schema_and_tools=True)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.requests.append(llm_request)
        if not self.script:
            raise AssertionError("el guion del modelo falso se agotó")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, LlmResponse):
            yield item
            return
        if isinstance(item, dict):
            item = json.dumps(item, ensure_ascii=False)
        part = types.Part(text=item) if isinstance(item, str) else item
        yield LlmResponse(content=types.Content(role="model", parts=[part]), usage_metadata=usage())


def system_text(request: LlmRequest) -> str:
    instruction = request.config.system_instruction
    return instruction if isinstance(instruction, str) else str(instruction)


def contents(request: LlmRequest) -> list[tuple[str, str]]:
    """(rol, texto) de cada mensaje enviado al modelo; las partes de tool como `call:`/`result:`."""
    out = []
    for content in request.contents:
        for part in content.parts or []:
            if part.text is not None:
                out.append((content.role, part.text))
            elif part.function_call:
                out.append((content.role, f"call:{part.function_call.name}"))
            elif part.function_response:
                out.append((content.role, f"result:{json.dumps(part.function_response.response)}"))
    return out
