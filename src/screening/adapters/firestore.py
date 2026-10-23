"""Persistencia en Firestore: fichas de candidatos y mensajes entrantes (spec 008).

El cliente se inyecta (en los tests, un fake en memoria). Los documentos se guardan con
`model_dump(mode="json")`, así que las fechas quedan en ISO 8601.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from google.api_core.exceptions import AlreadyExists
from google.cloud.firestore_v1.base_query import FieldFilter

from screening.channels import InboundMessage
from screening.domain import Candidate, CandidateStatus

if TYPE_CHECKING:
    from google.cloud import firestore


class FirestoreCandidateRepository:
    """Implementa `CandidateRepository` con un documento por ficha (`{collection}/{id}`)."""

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
        self._collection.document(candidate.id).set(candidate.model_dump(mode="json"))

    def list(self, status: CandidateStatus | None = None) -> list[Candidate]:
        query = self._collection
        if status is not None:
            query = query.where(filter=FieldFilter("status", "==", status.value))
        return [Candidate.model_validate(s.to_dict()) for s in query.stream()]


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
