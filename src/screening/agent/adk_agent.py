"""`ScreeningAgent` sobre Google ADK: un `LlmAgent` extrae y otro redacta (ver spec 009)."""

from __future__ import annotations

import contextvars
import logging
import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.genai import types
from pydantic import ValidationError

from screening.adapters.llm_pricing import PricingTable, load_pricing
from screening.agent.context import (
    NO_NEW_MESSAGES,
    build_extraction_input,
    build_turn_instructions,
    local_today,
    region_for,
    split_for_reply,
)
from screening.agent.extraction import ExtractionResponse, build_outcome
from screening.agent.guards import FAQ_TOOL_NAME, find_internal_leak
from screening.agent.prompts import DEFAULT_PROMPTS_DIR, load_agent_prompts
from screening.agent.runtime import AgentRun, run_agent
from screening.agent.sentiment import SentimentClassifier, SentimentResult
from screening.agent.tracing import configure_tracing, tracing_from_env
from screening.clock import Clock
from screening.config import AppConfig
from screening.config.models import RegionConfig
from screening.domain import Candidate, Message, MessageRole
from screening.knowledge.chunking import faq_for_prompt, load_chunks
from screening.ports import (
    ExtractionOutcome,
    KnowledgeBase,
    LLMError,
    LLMUsage,
    ReplyOutcome,
    ReplyRequest,
)

logger = logging.getLogger(__name__)

_OUTPUT_ATTEMPTS = 2  # una salida inválida se reintenta una sola vez
_FAQ_LIMIT = 3
_DEFAULT_TIMEOUT_SECONDS = 60.0


class _InvalidOutput(Exception):
    """El modelo respondió, pero la salida no sirve (vacía, mal formada o con fugas)."""


class AdkScreeningAgent:
    """Implementa `ScreeningAgent`. Sin estado: cada llamada arma su contexto desde la ficha."""

    def __init__(
        self,
        config: AppConfig,
        knowledge: KnowledgeBase,
        *,
        clock: Clock,
        pricing: PricingTable,
        extraction_model: BaseLlm,
        reply_model: BaseLlm,
        prompts_dir: Path | str = DEFAULT_PROMPTS_DIR,
        sentiment_classifier: SentimentClassifier | None = None,
        faq_text: str | None = None,
        tracing: bool | None = None,
        history_limit: int = 20,
        timer: Callable[[], float] = time.perf_counter,
    ) -> None:
        """`faq_text`: la FAQ completa en el prompt de respuesta, sin tool de búsqueda.

        Con una FAQ chica es más rápido (sin un paso de tool) y más confiable (Sofía la tiene
        siempre, aunque la extracción no marque la pregunta). Sin `faq_text`, Sofía busca en
        `knowledge` con la tool (RAG): el camino para una FAQ grande.
        """
        self.config = config
        self.knowledge = knowledge
        self.faq_text = faq_text.strip() if faq_text else None
        self.clock = clock
        self.pricing = pricing
        self.extraction_model = extraction_model
        self.reply_model = reply_model
        self.sentiment_classifier = sentiment_classifier
        self.prompts = load_agent_prompts(prompts_dir)
        self.history_limit = history_limit
        self._timer = timer
        # Se renderizan al arrancar: un placeholder sin valor falla aquí y no en un turno.
        self._extraction_prompt = self.prompts.extraction.render()
        self._reply_prompt = self.prompts.reply.render(sla_hours=config.client.recruiter_sla_hours)
        if self.faq_text:
            # Antes de las instrucciones del turno: prefijo estable que OpenAI cachea.
            self._reply_prompt += f"\n\n## FAQ de {config.client.name}\n\n{self.faq_text}"
        for region in config.regions.values():
            self._system_prompt(region)

        if tracing_from_env() if tracing is None else tracing:
            configure_tracing()

    @classmethod
    def from_env(
        cls,
        config: AppConfig,
        knowledge: KnowledgeBase,
        *,
        clock: Clock,
        pricing: PricingTable | None = None,
        num_retries: int = 2,
        **kwargs: Any,
    ) -> AdkScreeningAgent:
        """Modelos de OpenAI de `config/client.yaml` vía LiteLLM (`OPENAI_API_KEY` del entorno).

        LiteLLM reintenta los errores transitorios (`num_retries`); `llm.reasoning_effort` se
        descarta solo en los modelos que no lo aceptan. Con `OPENAI_API_KEY` se suma el
        clasificador de emoción (014), salvo que se pase `sentiment_classifier`.
        """
        from google.adk.models.lite_llm import LiteLlm

        def model(name: str, reasoning_effort: str | None) -> LiteLlm:
            extra = {"reasoning_effort": reasoning_effort} if reasoning_effort else {}
            return LiteLlm(
                model=f"openai/{name}",
                num_retries=num_retries,
                timeout=_DEFAULT_TIMEOUT_SECONDS,
                drop_params=True,
                **extra,
            )

        if "sentiment_classifier" not in kwargs and os.environ.get("OPENAI_API_KEY"):
            kwargs["sentiment_classifier"] = SentimentClassifier.from_env(config)
        if "faq_text" not in kwargs:
            kwargs["faq_text"] = faq_for_prompt(load_chunks())

        return cls(
            config,
            knowledge,
            clock=clock,
            pricing=pricing or load_pricing(),
            extraction_model=model(
                config.llm.extraction_model,
                config.llm.extraction_reasoning_effort or config.llm.reasoning_effort,
            ),
            reply_model=model(config.llm.conversation_model, config.llm.reasoning_effort),
            **kwargs,
        )

    # --- Extracción ---------------------------------------------------------------

    def extract(
        self,
        candidate: Candidate,
        new_messages: list[Message],
        expected_fields: list[str],
    ) -> ExtractionOutcome:
        if not any(m.text.strip() for m in new_messages):
            return ExtractionOutcome(language=candidate.language, sentiment=candidate.sentiment)

        region = region_for(candidate, self.config)
        user_input = build_extraction_input(
            candidate,
            new_messages,
            expected_fields,
            region=region,
            today=local_today(self.clock.now(), region.timezone),
            agent_name=self.config.client.agent_name,
            history_limit=self.history_limit,
        )
        instruction = f"{self._system_prompt(region)}\n\n{self._extraction_prompt}"
        temperature = self.config.llm.extraction_temperature

        def attempt() -> AgentRun:
            agent = LlmAgent(
                name="extractor",
                model=self.extraction_model,
                instruction=lambda _ctx: instruction,
                output_schema=ExtractionResponse,
                include_contents="none",
                generate_content_config=(
                    types.GenerateContentConfig(temperature=temperature)
                    if temperature is not None
                    else None
                ),
            )
            return run_agent(
                agent, history=[], new_message=user_input, pricing=self.pricing, timer=self._timer
            )

        # El clasificador corre en paralelo con la extracción: no suma su latencia al turno.
        with ThreadPoolExecutor(max_workers=1) as pool:
            sentiment = (
                # Con el contexto del turno: su traza queda dentro del turno en LangSmith.
                pool.submit(contextvars.copy_context().run, self._classify, candidate, new_messages)
                if self.sentiment_classifier
                else None
            )
            response, usage = self._with_retry(attempt, _parse_extraction)
            mood = sentiment.result() if sentiment else SentimentResult(label=None)
        outcome = build_outcome(
            response,
            licenses=list(region.licenses),
            expected_fields=expected_fields,
            current=candidate.data,
        )
        if mood.label is not None:  # sin etiqueta confiable queda la heurística de la extracción
            outcome.sentiment = mood.label
        outcome.usage = [*usage, *mood.usage]
        return outcome

    # --- Respuesta ----------------------------------------------------------------

    def reply(self, request: ReplyRequest) -> ReplyOutcome:
        candidate = request.candidate
        region = region_for(candidate, self.config)
        today = local_today(self.clock.now(), region.timezone)
        instruction = "\n\n".join(
            [
                self._system_prompt(region),
                self._reply_prompt,
                build_turn_instructions(request, today=today),
            ]
        )
        history, pending = split_for_reply(candidate.messages, self.history_limit)
        new_message = "\n".join(m.text.strip() for m in pending) or NO_NEW_MESSAGES
        # Con la FAQ en el prompt no hay tool: un paso menos de modelo por turno.
        tools = [] if self.faq_text else [_faq_tool(self.knowledge)]

        def attempt() -> AgentRun:
            agent = LlmAgent(
                name="sofia",
                model=self.reply_model,
                instruction=lambda _ctx: instruction,
                tools=tools,
            )
            return run_agent(
                agent,
                history=history,
                new_message=new_message,
                pricing=self.pricing,
                timer=self._timer,
            )

        text, usage = self._with_retry(attempt, _parse_reply)
        return ReplyOutcome(text=text, usage=usage)

    # --- Internos -----------------------------------------------------------------

    def _classify(self, candidate: Candidate, new_messages: list[Message]) -> SentimentResult:
        """Los mensajes del candidato, previos y nuevos; el clasificador toma los últimos."""
        assert self.sentiment_classifier is not None
        new_ids = {m.id for m in new_messages}
        previous = [
            m.text
            for m in candidate.messages
            if m.role == MessageRole.CANDIDATE and m.id not in new_ids
        ]
        return self.sentiment_classifier.classify([*previous, *(m.text for m in new_messages)])

    def _system_prompt(self, region: RegionConfig) -> str:
        vocabulary = region.vocabulary
        return self.prompts.system.render(
            agent_name=self.config.client.agent_name,
            client_name=self.config.client.name,
            form_of_address=region.form_of_address,
            country=region.country.value,
            license_term=vocabulary.get("license", ""),
            car_term=vocabulary.get("car", ""),
            motorcycle_term=vocabulary.get("motorcycle", ""),
        )

    def _with_retry[R](
        self, attempt: Callable[[], AgentRun], parse: Callable[[str | None], R]
    ) -> tuple[R, list[LLMUsage]]:
        """Reintenta una vez una salida inválida. El uso incluye todos los intentos."""
        usage: list[LLMUsage] = []
        last_error: Exception | None = None
        for _ in range(_OUTPUT_ATTEMPTS):
            run = attempt()
            usage.extend(run.usage)
            try:
                return parse(run.text), usage
            except (_InvalidOutput, ValidationError) as e:
                last_error = e
                logger.warning("salida inválida del agente: %s", e)
        raise LLMError(f"salida inválida del agente tras {_OUTPUT_ATTEMPTS} intentos: {last_error}")


def _parse_extraction(text: str | None) -> ExtractionResponse:
    if not text:
        raise _InvalidOutput("respuesta vacía")
    return ExtractionResponse.model_validate_json(_strip_fences(text))


def _parse_reply(text: str | None) -> str:
    text = (text or "").strip()
    if not text:
        raise _InvalidOutput("respuesta vacía")
    leak = find_internal_leak(text)
    if leak:
        raise _InvalidOutput(f"el mensaje filtra texto interno: {leak!r}")
    return text


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    return text


def _faq_tool(knowledge: KnowledgeBase) -> Callable[[str], dict[str, Any]]:
    def search_faq(query: str) -> dict[str, Any]:
        """Busca en la FAQ de la empresa la respuesta a una pregunta del candidato.

        Args:
            query: la pregunta del candidato, breve y en español.
        """
        try:
            snippets = knowledge.search(query, limit=_FAQ_LIMIT)
        except Exception as e:  # sin FAQ, Sofía dice que lo consulta con el equipo
            logger.warning("falló la búsqueda en la FAQ: %s", e)
            snippets = []
        return {
            "found": bool(snippets),
            "results": [{"source": s.source, "text": s.text} for s in snippets],
        }

    search_faq.__name__ = FAQ_TOOL_NAME
    return search_faq
