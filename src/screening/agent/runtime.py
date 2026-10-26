"""Corre un `LlmAgent` de ADK sin estado y convierte el uso de sus eventos a `LLMUsage`."""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from screening.adapters.llm_pricing import PricingTable
from screening.domain import Message, MessageRole
from screening.ports import LLMError, LLMUsage

APP_NAME = "screening"
USER_ID = "candidate"


@dataclass
class AgentRun:
    """Texto final del agente (sin partes de razonamiento) y una entrada de uso por llamada."""

    text: str | None
    usage: list[LLMUsage] = field(default_factory=list)


def model_name(model: str) -> str:
    """`openai/gpt-5-mini` → `gpt-5-mini`, el nombre con el que se busca el precio."""
    return model.rsplit("/", 1)[-1]


def usage_from_metadata(
    metadata: types.GenerateContentResponseUsageMetadata,
    *,
    model: str,
    latency_ms: float,
    pricing: PricingTable,
) -> LLMUsage:
    """`candidates_token_count` ya incluye los tokens de razonamiento (LiteLLM los copia de
    `completion_tokens`), así que no se suma `thoughts_token_count`."""
    name = model_name(model)
    input_tokens = metadata.prompt_token_count or 0
    output_tokens = metadata.candidates_token_count or 0
    cached = metadata.cached_content_token_count or 0
    return LLMUsage(
        model=name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        cost_usd=pricing.cost(name, input_tokens, output_tokens, cached),
    )


def run_agent(
    agent: LlmAgent,
    *,
    history: list[Message],
    new_message: str,
    pricing: PricingTable,
    timer: Callable[[], float],
) -> AgentRun:
    """Una sesión nueva por llamada: el historial viene de la ficha, no de ADK."""
    return run_sync(lambda: _run(agent, history, new_message, pricing, timer))


def run_sync[R](factory: Callable[[], Coroutine[Any, Any, R]]) -> R:
    """El puerto es síncrono; si ya hay un event loop (FastAPI), se corre en otro hilo."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())
    # Con el contexto actual: la traza de ADK queda dentro de la del turno en LangSmith.
    context = contextvars.copy_context()
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(context.run, lambda: asyncio.run(factory())).result()


async def _run(
    agent: LlmAgent,
    history: list[Message],
    new_message: str,
    pricing: PricingTable,
    timer: Callable[[], float],
) -> AgentRun:
    service = InMemorySessionService()
    session = await service.create_session(app_name=APP_NAME, user_id=USER_ID)
    for message in history:
        await service.append_event(session, _history_event(message, agent.name))

    runner = Runner(agent=agent, app_name=APP_NAME, session_service=service)
    model = agent.model if isinstance(agent.model, str) else agent.model.model
    result = AgentRun(text=None)
    mark = timer()
    try:
        async for event in runner.run_async(
            user_id=USER_ID, session_id=session.id, new_message=_content("user", new_message)
        ):
            if event.error_code:
                raise LLMError(
                    f"{model} devolvió un error: {event.error_code} {event.error_message}"
                )
            if event.partial:
                continue
            now = timer()
            if event.usage_metadata is not None:
                result.usage.append(
                    usage_from_metadata(
                        event.usage_metadata,
                        model=model,
                        latency_ms=(now - mark) * 1000,
                        pricing=pricing,
                    )
                )
            mark = now  # la latencia de la próxima llamada no incluye la ejecución de tools
            if event.is_final_response():
                result.text = _text(event)
    except LLMError:
        raise
    except Exception as e:
        raise LLMError(f"falló la llamada a {model}: {type(e).__name__}: {e}") from e
    finally:
        await runner.close()
    return result


def _history_event(message: Message, agent_name: str) -> Event:
    if message.role == MessageRole.AGENT:
        return Event(
            author=agent_name, invocation_id="history", content=_content("model", message.text)
        )
    return Event(author="user", invocation_id="history", content=_content("user", message.text))


def _content(role: str, text: str) -> types.Content:
    return types.Content(role=role, parts=[types.Part(text=text)])


def _text(event: Event) -> str | None:
    if event.content is None or not event.content.parts:
        return None
    text = "".join(p.text for p in event.content.parts if p.text and not p.thought)
    return text or None
