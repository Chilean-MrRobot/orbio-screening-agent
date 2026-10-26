"""Una traza por turno en LangSmith, agrupada por candidato (vista Threads).

Las trazas de ADK y OpenAI muestran el detalle técnico (la salida cruda del modelo trae todas
las propiedades, con `null` donde el candidato no dijo nada: lo exige el modo estricto). Esta
traza es la vista de negocio de cada turno: el mensaje del candidato, la respuesta de Sofía y
solo los datos que cambiaron. Todas llevan `thread_id = candidate.id`, así LangSmith las
agrupa como una conversación. Sin `LANGSMITH_TRACING=true` y una key, no hace nada.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from screening.domain import Candidate, MessageRole

logger = logging.getLogger(__name__)


def tracing_enabled() -> bool:
    return os.environ.get("LANGSMITH_TRACING", "").strip().lower() == "true" and bool(
        os.environ.get("LANGSMITH_API_KEY", "").strip()
    )


class TurnTrace:
    """Lo que el orquestador sabe al empezar el turno; al cerrarlo calcula la salida."""

    def __init__(self, candidate: Candidate, inputs: dict[str, Any]) -> None:
        self.candidate = candidate
        self.inputs = inputs
        self._data_before = candidate.data.model_dump(mode="json")
        self._messages_before = len(candidate.messages)

    def outputs(self) -> dict[str, Any]:
        candidate = self.candidate
        after = candidate.data.model_dump(mode="json")
        changed = {
            name: value
            for name, value in after.items()
            if value not in (None, []) and value != self._data_before.get(name)
        }
        replies = [
            m.text
            for m in candidate.messages[self._messages_before :]
            if m.role == MessageRole.AGENT
        ]
        out: dict[str, Any] = {
            "respuesta": "\n\n".join(replies) or None,
            "datos_nuevos": changed,
            "etapa": candidate.stage.value,
            "estado": candidate.status.value,
            "idioma": candidate.language.value,
            "animo": candidate.sentiment.value,
        }
        if candidate.knockout is not None and not candidate.knockout.passed:
            out["descartado_por"] = candidate.knockout.reason_code
        if candidate.score is not None:
            out["puntaje"] = candidate.score.total
        if candidate.data.needs_review:
            out["revisar"] = candidate.data.needs_review
        return out

    def metadata(self) -> dict[str, Any]:
        return {
            "thread_id": self.candidate.id,  # LangSmith agrupa los turnos en un hilo
            "candidate_id": self.candidate.id,
            "country": self.candidate.country.value if self.candidate.country else None,
            "source": self.candidate.application.source,
        }


@contextmanager
def trace_turn(name: str, candidate: Candidate, inputs: dict[str, Any]) -> Iterator[TurnTrace]:
    """Envuelve un turno. Si LangSmith falla, el turno sigue igual (solo se pierde la traza)."""
    turn = TurnTrace(candidate, inputs)
    if not tracing_enabled():
        yield turn
        return
    try:
        from langsmith import trace

        context = trace(
            name=name,
            run_type="chain",
            inputs=inputs,
            metadata=turn.metadata(),
            tags=["turno", candidate.stage.value],
            project_name=os.environ.get("LANGSMITH_PROJECT") or None,
        )
        run = context.__enter__()
    except Exception:
        logger.warning("No se pudo abrir la traza del turno en LangSmith", exc_info=True)
        yield turn
        return

    error: BaseException | None = None
    try:
        yield turn
    except BaseException as exc:
        error = exc
        raise
    finally:
        try:
            run.end(outputs=turn.outputs())
            if error is None:
                context.__exit__(None, None, None)
            else:
                context.__exit__(type(error), error, error.__traceback__)
        except Exception:
            logger.warning("No se pudo cerrar la traza del turno en LangSmith", exc_info=True)
