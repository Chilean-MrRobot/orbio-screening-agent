"""Persistencia en Firestore: fichas de candidatos y mensajes entrantes (spec 008).

El cliente se inyecta (en los tests, un fake en memoria). Los documentos se guardan con
`model_dump(mode="json")`, así que las fechas quedan en ISO 8601.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from google.api_core.exceptions import AlreadyExists
from google.cloud.firestore_v1.base_query import FieldFilter

from screening.adapters.query import ORDER_FIELDS, decode_cursor, encode_cursor, index_fields
from screening.channels import InboundMessage
from screening.domain import Candidate, CandidateStatus
from screening.ports import CandidatePage, CandidateQuery
from screening.scheduling.slots import slot_id as make_slot_id

if TYPE_CHECKING:
    from google.cloud import firestore

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class FirestoreCandidateRepository:
    """Implementa `CandidateRepository` con un documento por ficha (`{collection}/{id}`).

    Junto a la ficha se guardan los campos `idx_*` (ver `adapters/query.py`); los índices
    compuestos que necesitan las consultas están en `deploy/firestore.indexes.json`.
    """

    def __init__(self, client: firestore.Client, collection: str = "candidates") -> None:
        self._collection = client.collection(collection)

    def get(self, candidate_id: str) -> Candidate | None:
        snapshot = self._collection.document(candidate_id).get()
        return Candidate.model_validate(snapshot.to_dict()) if snapshot.exists else None

    def get_by_phone(self, phone: str) -> Candidate | None:
        query = self._collection.where(filter=FieldFilter("phone", "==", phone)).limit(1)
        for snapshot in query.stream():
            return Candidate.model_validate(snapshot.to_dict())
        return None

    def save(self, candidate: Candidate) -> None:
        document = candidate.model_dump(mode="json") | index_fields(candidate)
        self._collection.document(candidate.id).set(document)

    def query(self, query: CandidateQuery) -> CandidatePage:
        from google.cloud import firestore

        if query.statuses == []:
            return CandidatePage(items=[])
        field, descending = ORDER_FIELDS[query.order]
        direction = firestore.Query.DESCENDING if descending else firestore.Query.ASCENDING

        ref = self._collection
        if query.statuses is not None:
            statuses = [s.value for s in query.statuses]
            ref = ref.where(filter=FieldFilter("status", "in", statuses))
        if query.country is not None:
            ref = ref.where(filter=FieldFilter("country", "==", query.country.value))
        if query.stage is not None:
            ref = ref.where(filter=FieldFilter("stage", "==", query.stage.value))
        if query.order == "next_touch_asc" or query.touch_due_before is not None:
            # Un filtro de rango deja fuera las fichas sin próximo toque (`null`).
            ref = ref.where(filter=FieldFilter("idx_next_touch_at", ">=", _EPOCH))
        if query.touch_due_before is not None:
            ref = ref.where(filter=FieldFilter("idx_next_touch_at", "<=", query.touch_due_before))
        ref = ref.order_by(field, direction=direction).order_by("id", direction=direction)
        if query.cursor is not None:
            value, last_id = decode_cursor(query.cursor, query.order)
            ref = ref.start_after({field: value, "id": last_id})

        snapshots = list(ref.limit(query.limit + 1).stream())
        items = [Candidate.model_validate(s.to_dict()) for s in snapshots[: query.limit]]
        next_cursor = None
        if len(snapshots) > query.limit:
            tail = snapshots[query.limit - 1].to_dict()
            next_cursor = encode_cursor(query.order, tail[field], tail["id"])
        return CandidatePage(items=items, next_cursor=next_cursor)

    def count_by_status(self) -> dict[CandidateStatus, int]:
        """Agregaciones `count()` por estado: no lee las fichas."""
        counts: dict[CandidateStatus, int] = {}
        for status in CandidateStatus:
            query = self._collection.where(filter=FieldFilter("status", "==", status.value))
            result = query.count().get()
            counts[status] = int(result[0][0].value)
        return counts

    def list(self, status: CandidateStatus | None = None) -> list[Candidate]:
        query = self._collection
        if status is not None:
            query = query.where(filter=FieldFilter("status", "==", status.value))
        return [Candidate.model_validate(s.to_dict()) for s in query.stream()]


class FirestoreLeaseStore:
    """Implementa `LeaseStore` con un documento por clave y una transacción por intento."""

    def __init__(self, client: firestore.Client, collection: str = "leases") -> None:
        self._client = client
        self._collection = client.collection(collection)

    def try_acquire(self, key: str, owner: str, *, now: datetime, ttl: timedelta) -> bool:
        from google.cloud import firestore

        ref = self._collection.document(key)

        @firestore.transactional
        def acquire(transaction: firestore.Transaction) -> bool:
            snapshot = ref.get(transaction=transaction)
            current = snapshot.to_dict() if snapshot.exists else None
            if current and current["owner"] != owner and current["expires_at"] > now:
                return False
            transaction.set(ref, {"owner": owner, "expires_at": now + ttl})
            return True

        return acquire(self._client.transaction())

    def release(self, key: str, owner: str) -> None:
        from google.cloud import firestore

        ref = self._collection.document(key)

        @firestore.transactional
        def release(transaction: firestore.Transaction) -> None:
            snapshot = ref.get(transaction=transaction)
            if snapshot.exists and snapshot.to_dict()["owner"] == owner:
                transaction.delete(ref)

        release(self._client.transaction())


class FirestoreInterviewSlotStore:
    """Implementa `InterviewSlotStore` (spec 021): un documento por horario reservado.

    `create` falla si el documento existe, así que dos instancias no reservan el mismo
    horario. `reserved_between` es un rango sobre `slot_id` (índice simple automático).
    """

    def __init__(self, client: firestore.Client, collection: str = "interview_slots") -> None:
        self._collection = client.collection(collection)

    def try_reserve(
        self, slot_id: str, candidate_id: str, *, start: datetime, now: datetime
    ) -> bool:
        ref = self._collection.document(slot_id)
        document = {
            "slot_id": slot_id,
            "candidate_id": candidate_id,
            "start": start.isoformat(),
            "reserved_at": now.isoformat(),
        }
        try:
            ref.create(document)
        except AlreadyExists:
            snapshot = ref.get()
            return snapshot.exists and snapshot.to_dict()["candidate_id"] == candidate_id
        return True

    def release(self, slot_id: str, candidate_id: str) -> None:
        # Sin transacción: solo el dueño borra, y nadie más puede crear el documento mientras
        # exista, así que entre leer y borrar no cambia de dueño.
        ref = self._collection.document(slot_id)
        snapshot = ref.get()
        if snapshot.exists and snapshot.to_dict()["candidate_id"] == candidate_id:
            ref.delete()

    def reserved_between(self, scope: str, start: datetime, end: datetime) -> set[str]:
        query = self._collection.where(
            filter=FieldFilter("slot_id", ">=", make_slot_id(scope, start))
        ).where(filter=FieldFilter("slot_id", "<", make_slot_id(scope, end)))
        return {s.to_dict()["slot_id"] for s in query.stream()}


class FirestoreInboundMessageStore:
    """Implementa `InboundMessageStore`; el ID del documento es el wamid de Meta."""

    def __init__(self, client: firestore.Client, collection: str = "inbound_messages") -> None:
        self._client = client
        self._collection = client.collection(collection)

    def add(self, message: InboundMessage) -> bool:
        # `create` falla si el documento existe: deduplicación atómica por wamid.
        try:
            self._collection.document(message.external_id).create(message.model_dump(mode="json"))
        except AlreadyExists:
            return False
        return True

    def mark_processed(self, external_ids: list[str]) -> None:
        batch = self._client.batch()
        for external_id in external_ids:
            ref = self._collection.document(external_id)
            if ref.get().exists:
                batch.update(ref, {"processed": True})
        batch.commit()

    def get(self, external_id: str) -> InboundMessage | None:
        snapshot = self._collection.document(external_id).get()
        return InboundMessage.model_validate(snapshot.to_dict()) if snapshot.exists else None

    def list_unprocessed(self) -> list[InboundMessage]:
        query = self._collection.where(filter=FieldFilter("processed", "==", False))
        return [InboundMessage.model_validate(s.to_dict()) for s in query.stream()]

    def list_unprocessed_for(self, sender: str) -> list[InboundMessage]:
        # Solo igualdades: Firestore la resuelve con los índices simples, sin índice compuesto.
        query = self._collection.where(filter=FieldFilter("sender", "==", sender)).where(
            filter=FieldFilter("processed", "==", False)
        )
        return [InboundMessage.model_validate(s.to_dict()) for s in query.stream()]
