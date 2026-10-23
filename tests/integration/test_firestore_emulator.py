"""Ida y vuelta contra el emulador de Firestore (`gcloud emulators firestore start`)."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from screening.adapters.firestore import FirestoreCandidateRepository, FirestoreInboundMessageStore
from screening.channels import InboundMessage
from screening.domain import Candidate

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("FIRESTORE_EMULATOR_HOST"), reason="sin FIRESTORE_EMULATOR_HOST"
    ),
]


@pytest.fixture
def client():  # type: ignore[no-untyped-def]
    from google.cloud import firestore

    return firestore.Client(project=os.environ.get("FIRESTORE_PROJECT", "demo-orbio"))


def test_candidate_round_trip(client, candidate: Candidate) -> None:  # type: ignore[no-untyped-def]
    collection = f"candidates-test-{uuid4().hex[:8]}"
    repository = FirestoreCandidateRepository(client, collection=collection)
    candidate.phone = f"+3460{uuid4().int % 10**7:07d}"

    repository.save(candidate)

    assert repository.get(candidate.id) == candidate
    found = repository.get_by_phone(candidate.phone)
    assert found is not None and found.id == candidate.id


def test_inbound_dedup(client) -> None:  # type: ignore[no-untyped-def]
    store = FirestoreInboundMessageStore(client, collection=f"inbound-test-{uuid4().hex[:8]}")
    at = datetime.now(UTC)
    message = InboundMessage(
        external_id=f"wamid.{uuid4().hex}",
        sender="+34600111222",
        text="hola",
        type="text",
        sent_at=at,
        received_at=at,
    )

    assert store.add(message)
    assert not store.add(message)
    store.mark_processed([message.external_id])
    assert store.list_unprocessed() == []
