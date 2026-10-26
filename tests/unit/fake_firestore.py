"""Cliente Firestore falso en memoria: solo lo que usan los adaptadores."""

from __future__ import annotations

import copy
from typing import Any

from google.api_core.exceptions import AlreadyExists, NotFound


class FakeSnapshot:
    def __init__(self, doc_id: str, data: dict[str, Any] | None) -> None:
        self.id = doc_id
        self._data = data

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return copy.deepcopy(self._data)


class FakeDocument:
    def __init__(self, store: dict[str, dict[str, Any]], doc_id: str) -> None:
        self._store = store
        self.id = doc_id

    def get(self) -> FakeSnapshot:
        return FakeSnapshot(self.id, self._store.get(self.id))

    def set(self, data: dict[str, Any]) -> None:
        self._store[self.id] = copy.deepcopy(data)

    def create(self, data: dict[str, Any]) -> None:
        if self.id in self._store:
            raise AlreadyExists(f"{self.id} ya existe")
        self.set(data)

    def update(self, data: dict[str, Any]) -> None:
        if self.id not in self._store:
            raise NotFound(f"{self.id} no existe")
        self._store[self.id].update(copy.deepcopy(data))

    def delete(self) -> None:
        self._store.pop(self.id, None)


_OPERATORS = {
    "==": lambda a, b: a == b,
    ">=": lambda a, b: a is not None and a >= b,
    ">": lambda a, b: a is not None and a > b,
    "<=": lambda a, b: a is not None and a <= b,
    "<": lambda a, b: a is not None and a < b,
}


class FakeQuery:
    def __init__(self, store: dict[str, dict[str, Any]], filters: list[Any], limit: int | None):
        self._store = store
        self._filters = filters
        self._limit = limit

    def where(self, *, filter: Any) -> FakeQuery:
        assert filter.op_string in _OPERATORS
        return FakeQuery(self._store, [*self._filters, filter], self._limit)

    def limit(self, count: int) -> FakeQuery:
        return FakeQuery(self._store, self._filters, count)

    def stream(self) -> list[FakeSnapshot]:
        matches = [
            FakeSnapshot(doc_id, copy.deepcopy(data))
            for doc_id, data in self._store.items()
            if all(_OPERATORS[f.op_string](data.get(f.field_path), f.value) for f in self._filters)
        ]
        return matches[: self._limit] if self._limit is not None else matches


class FakeCollection(FakeQuery):
    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        super().__init__(store, [], None)

    def document(self, doc_id: str) -> FakeDocument:
        return FakeDocument(self._store, doc_id)


class FakeBatch:
    def __init__(self) -> None:
        self._ops: list[tuple[FakeDocument, dict[str, Any]]] = []

    def update(self, ref: FakeDocument, data: dict[str, Any]) -> None:
        self._ops.append((ref, data))

    def commit(self) -> None:
        for ref, data in self._ops:
            ref.update(data)


class FakeFirestoreClient:
    def __init__(self) -> None:
        self.collections: dict[str, dict[str, dict[str, Any]]] = {}

    def collection(self, name: str) -> FakeCollection:
        return FakeCollection(self.collections.setdefault(name, {}))

    def batch(self) -> FakeBatch:
        return FakeBatch()
