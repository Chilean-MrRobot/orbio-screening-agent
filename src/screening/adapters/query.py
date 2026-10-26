"""Semántica común de `CandidateQuery` para los repositorios (memoria y Firestore).

Firestore excluye de un `order_by` los documentos sin ese campo, así que el orden usa campos
de índice desnormalizados (`idx_*`) que siempre existen. La versión en memoria aplica las
mismas reglas para que los tests describan el comportamiento real.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

from screening.domain import Candidate
from screening.ports import CandidateOrder, CandidateQuery

# campo de índice y si el orden es descendente
ORDER_FIELDS: dict[CandidateOrder, tuple[str, bool]] = {
    "updated_desc": ("idx_updated_at", True),
    "score_desc": ("idx_score", True),
    "next_touch_asc": ("idx_next_touch_at", False),
}
NO_SCORE = -1.0  # las fichas sin puntaje van al final de `score_desc`


class InvalidCursorError(ValueError):
    """El cursor no salió de una página de esta misma consulta."""


def index_fields(candidate: Candidate) -> dict[str, Any]:
    """Campos que se guardan junto a la ficha solo para filtrar y ordenar."""
    return {
        "idx_updated_at": candidate.updated_at,
        "idx_score": candidate.score.total if candidate.score else NO_SCORE,
        "idx_next_touch_at": candidate.reengagement.next_touch_at,
    }


def order_value(candidate: Candidate, order: CandidateOrder) -> Any:
    return index_fields(candidate)[ORDER_FIELDS[order][0]]


def matches(candidate: Candidate, query: CandidateQuery) -> bool:
    if query.statuses is not None and candidate.status not in query.statuses:
        return False
    if query.country is not None and candidate.country != query.country:
        return False
    if query.stage is not None and candidate.stage != query.stage:
        return False
    next_touch = candidate.reengagement.next_touch_at
    if query.order == "next_touch_asc" and next_touch is None:
        return False  # sin próximo toque no entra en el orden (igual que en Firestore)
    return not (
        query.touch_due_before is not None
        and (next_touch is None or next_touch > query.touch_due_before)
    )


def encode_cursor(order: CandidateOrder, value: Any, candidate_id: str) -> str:
    raw = value.isoformat() if isinstance(value, datetime) else value
    payload = json.dumps(
        {"o": order, "v": raw, "d": isinstance(value, datetime), "id": candidate_id}
    )
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode_cursor(cursor: str, order: CandidateOrder) -> tuple[Any, str]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        if payload["o"] != order:
            raise InvalidCursorError("el cursor es de otro orden")
        value = payload["v"]
        if payload["d"] and value is not None:
            value = datetime.fromisoformat(value).astimezone(UTC)
        return value, str(payload["id"])
    except (ValueError, KeyError, TypeError) as e:
        if isinstance(e, InvalidCursorError):
            raise
        raise InvalidCursorError("cursor inválido") from e
