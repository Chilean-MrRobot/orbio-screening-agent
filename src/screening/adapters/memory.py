"""Repositorio en memoria para tests y desarrollo local."""

from __future__ import annotations

from screening.domain import Candidate, CandidateStatus


class InMemoryCandidateRepository:
    """Guarda copias para comportarse como una base de datos real (sin alias compartidos)."""

    def __init__(self) -> None:
        self._items: dict[str, Candidate] = {}

    def get(self, candidate_id: str) -> Candidate | None:
        candidate = self._items.get(candidate_id)
        return candidate.model_copy(deep=True) if candidate else None

    def get_by_phone(self, phone: str) -> Candidate | None:
        for candidate in self._items.values():
            if candidate.phone == phone:
                return candidate.model_copy(deep=True)
        return None

    def save(self, candidate: Candidate) -> None:
        self._items[candidate.id] = candidate.model_copy(deep=True)

    def list(self, status: CandidateStatus | None = None) -> list[Candidate]:
        return [
            c.model_copy(deep=True)
            for c in self._items.values()
            if status is None or c.status == status
        ]
