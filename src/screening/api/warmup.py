"""Calentamiento de los LLM al arrancar la API (spec 022).

En producción, el primer turno de cada instancia tardaba mucho más que los siguientes
(extracción 8,8 s frente a 2,6 s; clasificador 4,0 s frente a 0,8 s): imports perezosos de
LiteLLM y del SDK de OpenAI, el mapa de precios de LiteLLM y conexiones nuevas. Una llamada
mínima a cada modelo configurado al arrancar paga ese costo antes que el candidato.

Con `LLM_WARMUP=false` o sin `OPENAI_API_KEY` no se hace nada.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor

from screening.config import AppConfig

logger = logging.getLogger(__name__)

WARMUP_ENV = "LLM_WARMUP"
TIMEOUT_ENV = "LLM_WARMUP_TIMEOUT_SECONDS"
DEFAULT_TIMEOUT_SECONDS = 8.0


def warmup_enabled(env: Mapping[str, str] = os.environ) -> bool:
    off = (env.get(WARMUP_ENV) or "true").strip().lower() in {"0", "false", "no", "off"}
    return not off and bool(env.get("OPENAI_API_KEY"))


def warmup_timeout(env: Mapping[str, str] = os.environ) -> float:
    return float(env.get(TIMEOUT_ENV) or DEFAULT_TIMEOUT_SECONDS)


def warm_up_llms(
    config: AppConfig, *, complete: Callable[[str], object] | None = None
) -> dict[str, float]:
    """Una llamada mínima por modelo, en paralelo. Los errores se registran y no se propagan.

    Devuelve los segundos de cada modelo (para el log).
    """
    complete = complete or _litellm_complete
    models = list(
        dict.fromkeys(
            [
                config.llm.extraction_model,
                config.llm.conversation_model,
                config.llm.classifier_model,
            ]
        )
    )

    def timed(model: str) -> tuple[str, float]:
        started = time.monotonic()
        try:
            complete(model)
        except Exception as e:  # el calentamiento nunca debe impedir que la API arranque
            logger.warning("llm_warmup %s falló: %s", model, e)
        return model, round(time.monotonic() - started, 2)

    with ThreadPoolExecutor(max_workers=len(models)) as pool:
        timings = dict(pool.map(timed, models))
    logger.info("llm_warmup %s", timings)
    return timings


def _litellm_complete(model: str) -> None:
    """LiteLLM síncrono con el mínimo de tokens: inicializa lo mismo que usa el agente ADK.

    Síncrono a propósito: `asyncio.run` en varios hilos confunde al worker de logging global
    de LiteLLM (un event loop por hilo).
    """
    import litellm

    litellm.completion(
        model=f"openai/{model}",
        messages=[{"role": "user", "content": "ok"}],
        max_completion_tokens=16,
        reasoning_effort="minimal",
        drop_params=True,  # los modelos sin razonamiento no aceptan reasoning_effort
        timeout=10,
    )
