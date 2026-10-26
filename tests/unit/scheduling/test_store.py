"""Contrato de `InterviewSlotStore` en memoria y sobre el fake de Firestore (spec 021)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from screening.adapters.firestore import FirestoreInterviewSlotStore
from screening.adapters.memory import InMemoryInterviewSlotStore
from screening.ports import InterviewSlotStore
from screening.scheduling import slot_id
from tests.unit.fake_firestore import FakeFirestoreClient

START = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)


@pytest.fixture(params=["memory", "firestore"])
def store(request: pytest.FixtureRequest) -> InterviewSlotStore:
    if request.param == "memory":
        return InMemoryInterviewSlotStore()
    return FirestoreInterviewSlotStore(FakeFirestoreClient())  # type: ignore[arg-type]


def reserve(store: InterviewSlotStore, candidate: str, start: datetime = START) -> bool:
    return store.try_reserve(slot_id("ES", start), candidate, start=start, now=NOW)


def test_only_one_candidate_gets_a_slot(store: InterviewSlotStore) -> None:
    assert reserve(store, "ana")
    assert not reserve(store, "beto")


def test_concurrent_reservations_have_a_single_winner() -> None:
    store = InMemoryInterviewSlotStore()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda n: reserve(store, f"c{n}"), range(20)))

    assert results.count(True) == 1


def test_reserving_again_is_idempotent_for_the_same_candidate(store: InterviewSlotStore) -> None:
    assert reserve(store, "ana")
    assert reserve(store, "ana")


def test_release_only_frees_your_own_reservation(store: InterviewSlotStore) -> None:
    reserve(store, "ana")

    store.release(slot_id("ES", START), "beto")
    assert not reserve(store, "beto")

    store.release(slot_id("ES", START), "ana")
    assert reserve(store, "beto")


def test_reserved_between_filters_by_scope_and_range(store: InterviewSlotStore) -> None:
    later = START + timedelta(days=2)
    reserve(store, "ana")
    reserve(store, "beto", later)
    store.try_reserve(slot_id("MX", START), "carla", start=START, now=NOW)

    assert store.reserved_between("ES", START, START + timedelta(days=1)) == {slot_id("ES", START)}
    assert store.reserved_between("ES", START, later + timedelta(minutes=30)) == {
        slot_id("ES", START),
        slot_id("ES", later),
    }
    assert store.reserved_between("MX", START, later) == {slot_id("MX", START)}
    assert store.reserved_between("ES", START + timedelta(minutes=1), later) == set()


def test_firestore_keeps_one_document_per_slot() -> None:
    client = FakeFirestoreClient()
    store = FirestoreInterviewSlotStore(client)  # type: ignore[arg-type]

    reserve(store, "ana")

    (document,) = client.collections["interview_slots"].values()
    assert document["slot_id"] == "ES_20261026T0900Z"
    assert document["candidate_id"] == "ana"
