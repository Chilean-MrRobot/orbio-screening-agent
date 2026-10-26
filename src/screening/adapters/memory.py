"""Repositorio y leases en memoria para tests y desarrollo local (una sola instancia)."""

from __future__ import annotations

import threading
from collections import Counter
from datetime import datetime, timedelta

from screening.adapters.query import (
    ORDER_FIELDS,
    decode_cursor,
    encode_cursor,
    matches,
    order_value,
)
from screening.domain import Candidate, CandidateStatus
from screening.ports import CandidatePage, CandidateQuery


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

    def query(self, query: CandidateQuery) -> CandidatePage:
        _, descending = ORDER_FIELDS[query.order]

        def key(c: Candidate) -> tuple:
            return (order_value(c, query.order), c.id)

        selected = sorted(
            (c for c in self._items.values() if matches(c, query)), key=key, reverse=descending
        )
        if query.cursor is not None:
            last = decode_cursor(query.cursor, query.order)
            selected = [c for c in selected if (key(c) < last if descending else key(c) > last)]
        page = selected[: query.limit]
        next_cursor = None
        if len(selected) > query.limit:
            tail = page[-1]
            next_cursor = encode_cursor(query.order, order_value(tail, query.order), tail.id)
        return CandidatePage(items=[c.model_copy(deep=True) for c in page], next_cursor=next_cursor)

    def count_by_status(self) -> dict[CandidateStatus, int]:
        counts = Counter(c.status for c in self._items.values())
        return {status: counts.get(status, 0) for status in CandidateStatus}

    def list(self, status: CandidateStatus | None = None) -> list[Candidate]:
        return [
            c.model_copy(deep=True)
            for c in self._items.values()
            if status is None or c.status == status
        ]


class InMemoryLeaseStore:
    """`LeaseStore` de un solo proceso: correcto en tests y con una instancia."""

    def __init__(self) -> None:
        self._leases: dict[str, tuple[str, datetime]] = {}
        self._guard = threading.Lock()

    def try_acquire(self, key: str, owner: str, *, now: datetime, ttl: timedelta) -> bool:
        with self._guard:
            current = self._leases.get(key)
            if current is not None and current[0] != owner and current[1] > now:
                return False
            self._leases[key] = (owner, now + ttl)
            return True

    def release(self, key: str, owner: str) -> None:
        with self._guard:
            current = self._leases.get(key)
            if current is not None and current[0] == owner:
                del self._leases[key]
