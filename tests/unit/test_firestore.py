from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from screening.adapters.firestore import FirestoreCandidateRepository, FirestoreInboundMessageStore
from screening.channels import InboundMessage
from screening.domain import (
    Candidate,
    CandidateStatus,
    ChannelName,
    DeliveryExperience,
    Message,
    MessageRole,
)
from tests.unit.fake_firestore import FakeFirestoreClient

AT = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)


@pytest.fixture
def client() -> FakeFirestoreClient:
    return FakeFirestoreClient()


@pytest.fixture
def firestore_repository(client: FakeFirestoreClient) -> FirestoreCandidateRepository:
    return FirestoreCandidateRepository(client)  # type: ignore[arg-type]


@pytest.fixture
def store(client: FakeFirestoreClient) -> FirestoreInboundMessageStore:
    return FirestoreInboundMessageStore(client)  # type: ignore[arg-type]


def _inbound(external_id: str = "wamid.1") -> InboundMessage:
    return InboundMessage(
        external_id=external_id,
        sender="+34600111222",
        text="hola",
        type="text",
        sent_at=AT,
        received_at=AT,
    )


# --- Repositorio -----------------------------------------------------------------


def test_candidate_round_trips(
    firestore_repository: FirestoreCandidateRepository,
    client: FakeFirestoreClient,
    candidate: Candidate,
) -> None:
    candidate.data.start_date = date(2026, 11, 1)
    candidate.data.delivery_experience = DeliveryExperience(years=2, platforms=["Glovo"])
    candidate.messages.append(
        Message(role=MessageRole.CANDIDATE, text="hola", channel=ChannelName.WHATSAPP)
    )
    candidate.record_event(AT, "field_updated", field="city", value="Madrid", previous=None)
    firestore_repository.save(candidate)

    assert firestore_repository.get(candidate.id) == candidate
    stored = client.collections["candidates"][candidate.id]
    assert stored["data"]["start_date"] == "2026-11-01"  # serializable en Firestore


def test_get_missing_returns_none(firestore_repository: FirestoreCandidateRepository) -> None:
    assert firestore_repository.get("nope") is None
    assert firestore_repository.get_by_phone("+34000") is None


def test_get_by_phone(
    firestore_repository: FirestoreCandidateRepository, candidate: Candidate
) -> None:
    firestore_repository.save(candidate)

    found = firestore_repository.get_by_phone("+34600111222")
    assert found is not None and found.id == candidate.id


def test_save_overwrites(
    firestore_repository: FirestoreCandidateRepository, candidate: Candidate
) -> None:
    firestore_repository.save(candidate)
    candidate.set_status(CandidateStatus.CONTACTED, AT)
    firestore_repository.save(candidate)

    stored = firestore_repository.get(candidate.id)
    assert stored is not None and stored.status == CandidateStatus.CONTACTED


def test_list_filters_by_status(
    firestore_repository: FirestoreCandidateRepository, candidate: Candidate
) -> None:
    other = candidate.model_copy(deep=True, update={"id": "other", "phone": "+34600999888"})
    other.status = CandidateStatus.QUALIFIED
    firestore_repository.save(candidate)
    firestore_repository.save(other)

    assert len(firestore_repository.list()) == 2
    assert [c.id for c in firestore_repository.list(CandidateStatus.QUALIFIED)] == ["other"]


# --- Mensajes entrantes ----------------------------------------------------------


def test_add_deduplicates_by_external_id(store: FirestoreInboundMessageStore) -> None:
    assert store.add(_inbound())
    assert not store.add(_inbound())
    assert store.get("wamid.1") == _inbound()


def test_mark_processed_and_list_unprocessed(store: FirestoreInboundMessageStore) -> None:
    store.add(_inbound("wamid.1"))
    store.add(_inbound("wamid.2"))

    store.mark_processed(["wamid.1", "wamid.unknown"])

    assert [m.external_id for m in store.list_unprocessed()] == ["wamid.2"]
    message = store.get("wamid.1")
    assert message is not None and message.processed


def test_get_unknown_message(store: FirestoreInboundMessageStore) -> None:
    assert store.get("nope") is None
