from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from screening.api.inbound_tasks import (
    PROCESS_INBOUND_PATH,
    InboundBatchProcessor,
    InboundScheduler,
    build_inbound_tasks_router,
    inbound_task_id,
)
from screening.channels import (
    CandidateBusyError,
    InboundBatch,
    InboundMessage,
    InMemoryInboundMessageStore,
)
from screening.clock import FixedClock
from screening.tasks import TASK_TOKEN_HEADER

SENDER = "+34600111222"
OTHER = "+525512345678"
TOKEN = "task-token"
AUTH = {TASK_TOKEN_HEADER: TOKEN}
T0 = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)


def inbound(external_id: str, text: str, sender: str = SENDER, second: int = 0) -> InboundMessage:
    at = T0 + timedelta(seconds=second)
    return InboundMessage(
        external_id=external_id, sender=sender, text=text, type="text", sent_at=at, received_at=at
    )


class RecordingHandler:
    def __init__(self) -> None:
        self.batches: list[InboundBatch] = []
        self.error: Exception | None = None
        self.during: Callable[[], None] | None = None

    def handle(self, batch: InboundBatch) -> None:
        if self.during is not None:
            self.during()
        if self.error is not None:
            raise self.error
        self.batches.append(batch)


class RecordingQueue:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object], float, str]] = []

    def enqueue(
        self, path: str, payload: dict[str, object], *, delay_seconds: float, task_id: str
    ) -> bool:
        created = all(task_id != call[3] for call in self.calls)
        self.calls.append((path, payload, delay_seconds, task_id))
        return created


@pytest.fixture
def store() -> InMemoryInboundMessageStore:
    return InMemoryInboundMessageStore()


@pytest.fixture
def handler() -> RecordingHandler:
    return RecordingHandler()


@pytest.fixture
def client(store: InMemoryInboundMessageStore, handler: RecordingHandler) -> TestClient:
    app = FastAPI()
    processor = InboundBatchProcessor(store, handler)
    app.include_router(build_inbound_tasks_router(processor, SecretStr(TOKEN)))
    return TestClient(app)


def call(client: TestClient, sender: str = SENDER, headers: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
    return client.post(
        PROCESS_INBOUND_PATH, json={"sender": sender}, headers=AUTH if headers is None else headers
    )


# --- Nombre de la tarea --------------------------------------------------------------


def test_task_id_is_stable_within_the_window() -> None:
    start = datetime(2026, 10, 22, 10, 0, 0, tzinfo=UTC)  # múltiplo de 8 s desde epoch
    assert start.timestamp() % 8 == 0

    first = inbound_task_id(SENDER, start, 8)

    assert inbound_task_id(SENDER, start + timedelta(seconds=7.9), 8) == first
    assert inbound_task_id(SENDER, start + timedelta(seconds=8), 8) != first
    assert inbound_task_id(OTHER, start, 8) != first


def test_task_id_hides_the_phone_and_is_a_valid_name() -> None:
    task_id = inbound_task_id(SENDER, T0, 8)

    assert "34600111222" not in task_id
    assert task_id.replace("-", "").isalnum()
    assert len(task_id) < 100


def test_task_id_with_zero_window_still_works() -> None:
    assert inbound_task_id(SENDER, T0, 0) != inbound_task_id(SENDER, T0 + timedelta(seconds=1), 0)


def test_scheduler_enqueues_one_task_per_sender_and_window(clock: FixedClock) -> None:
    queue = RecordingQueue()
    scheduler = InboundScheduler(queue, window_seconds=8, clock=clock)

    assert scheduler.schedule(SENDER)
    assert not scheduler.schedule(SENDER)

    path, payload, delay, task_id = queue.calls[0]
    assert path == PROCESS_INBOUND_PATH
    assert payload == {"sender": SENDER}
    assert delay == 8
    assert task_id == inbound_task_id(SENDER, clock.now(), 8)


# --- Endpoint ------------------------------------------------------------------------


@pytest.mark.parametrize("headers", [{}, {TASK_TOKEN_HEADER: "otro"}])
def test_endpoint_requires_the_token(
    client: TestClient,
    store: InMemoryInboundMessageStore,
    handler: RecordingHandler,
    headers: dict[str, str],
) -> None:
    store.add(inbound("w1", "hola"))

    response = call(client, headers=headers)

    assert response.status_code == 401
    assert handler.batches == []
    assert [m.external_id for m in store.list_unprocessed()] == ["w1"]


@pytest.mark.parametrize("body", [{}, {"sender": ""}, {"sender": 3}, ["x"]])
def test_endpoint_rejects_invalid_body(client: TestClient, body: object) -> None:
    response = client.post(PROCESS_INBOUND_PATH, json=body, headers=AUTH)

    assert response.status_code == 400


def test_endpoint_processes_pending_messages_of_the_sender_as_one_batch(
    client: TestClient, store: InMemoryInboundMessageStore, handler: RecordingHandler
) -> None:
    store.add(inbound("w2", "soy Lucía", second=3))
    store.add(inbound("w1", "hola", second=0))
    store.add(inbound("w3", "tengo moto", second=6))
    store.add(inbound("x1", "buenas", sender=OTHER))

    response = call(client)

    assert response.status_code == 200
    assert response.json() == {"status": "processed", "messages": 3}
    [batch] = handler.batches
    assert batch.sender == SENDER
    assert batch.text == "hola\nsoy Lucía\ntengo moto"
    assert [m.external_id for m in store.list_unprocessed()] == ["x1"]


def test_endpoint_without_pending_messages_is_ok_and_idempotent(
    client: TestClient, store: InMemoryInboundMessageStore, handler: RecordingHandler
) -> None:
    store.add(inbound("w1", "hola"))
    assert call(client).json()["status"] == "processed"

    response = call(client)

    assert response.status_code == 200
    assert response.json() == {"status": "empty"}
    assert len(handler.batches) == 1


def test_busy_candidate_returns_503_and_keeps_messages(
    client: TestClient, store: InMemoryInboundMessageStore, handler: RecordingHandler
) -> None:
    store.add(inbound("w1", "hola"))
    handler.error = CandidateBusyError(SENDER)

    response = call(client)

    assert response.status_code == 503
    assert "Retry-After" in response.headers
    assert [m.external_id for m in store.list_unprocessed()] == ["w1"]

    handler.error = None  # el reintento de Cloud Tasks ya encuentra el lease libre
    assert call(client).status_code == 200
    assert store.list_unprocessed() == []


def test_handler_failure_returns_503_and_keeps_messages(
    client: TestClient,
    store: InMemoryInboundMessageStore,
    handler: RecordingHandler,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store.add(inbound("w1", "hola"))
    handler.error = RuntimeError("boom")

    response = call(client)

    assert response.status_code == 503
    assert "boom" in caplog.text
    assert [m.external_id for m in store.list_unprocessed()] == ["w1"]


def test_message_arriving_during_processing_goes_to_the_next_batch(
    client: TestClient, store: InMemoryInboundMessageStore, handler: RecordingHandler
) -> None:
    store.add(inbound("w1", "hola"))
    handler.during = lambda: store.add(inbound("w2", "¿sigues ahí?", second=10))

    assert call(client).json() == {"status": "processed", "messages": 1}
    assert [m.external_id for m in store.list_unprocessed()] == ["w2"]

    handler.during = None
    assert call(client).json() == {"status": "processed", "messages": 1}

    assert [[m.external_id for m in b.messages] for b in handler.batches] == [["w1"], ["w2"]]
    assert store.list_unprocessed() == []
