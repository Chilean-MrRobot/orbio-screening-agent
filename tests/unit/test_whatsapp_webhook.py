from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from screening.api import WhatsAppSettings, create_app
from screening.channels import InboundBatch, InMemoryInboundMessageStore
from screening.clock import FixedClock
from tests.unit.fake_timer import FakeTimer, eventually, run

APP_SECRET = "app-secret"
VERIFY_TOKEN = "verify-me"
PHONE_NUMBER_ID = "PHONE_ID"
URL = "/webhooks/whatsapp"


class RecordingHandler:
    def __init__(self, fail: bool = False) -> None:
        self.batches: list[InboundBatch] = []
        self.fail = fail

    def handle(self, batch: InboundBatch) -> None:
        if self.fail:
            raise RuntimeError("boom")
        self.batches.append(batch)


@pytest.fixture
def settings() -> WhatsAppSettings:
    return WhatsAppSettings(
        verify_token=SecretStr(VERIFY_TOKEN),
        app_secret=SecretStr(APP_SECRET),
        phone_number_id=PHONE_NUMBER_ID,
        batch_seconds=8,
    )


@pytest.fixture
def store() -> InMemoryInboundMessageStore:
    return InMemoryInboundMessageStore()


@pytest.fixture
def handler() -> RecordingHandler:
    return RecordingHandler()


@pytest.fixture
def app(
    settings: WhatsAppSettings,
    store: InMemoryInboundMessageStore,
    handler: RecordingHandler,
    clock: FixedClock,
) -> FastAPI:
    return create_app(settings, store=store, handler=handler, clock=clock)


def sign(body: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def text_message(wamid: str, text: str, sender: str = "34600111222") -> dict[str, Any]:
    return {
        "from": sender,
        "id": wamid,
        "timestamp": "1792663200",
        "type": "text",
        "text": {"body": text},
    }


def webhook_payload(
    messages: list[dict[str, Any]] | None = None,
    statuses: list[dict[str, Any]] | None = None,
    phone_number_id: str = PHONE_NUMBER_ID,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "15550000000", "phone_number_id": phone_number_id},
    }
    if messages is not None:
        value["contacts"] = [{"profile": {"name": "Lucía"}, "wa_id": messages[0]["from"]}]
        value["messages"] = messages
    if statuses is not None:
        value["statuses"] = statuses
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA_ID", "changes": [{"field": "messages", "value": value}]}],
    }


def signed_post(payload: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    return body, {"Content-Type": "application/json", "X-Hub-Signature-256": sign(body)}


# --- Verificación (GET) ------------------------------------------------------------


def test_verification_returns_challenge(app: FastAPI) -> None:
    params = {"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "1158"}

    response = TestClient(app).get(URL, params=params)

    assert response.status_code == 200
    assert response.text == "1158"


def test_verification_with_wrong_token_is_forbidden(app: FastAPI) -> None:
    params = {"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1158"}

    response = TestClient(app).get(URL, params=params)

    assert response.status_code == 403


def test_healthz(app: FastAPI) -> None:
    assert TestClient(app).get("/healthz").json() == {"status": "ok"}


# --- Mensajes (POST) ---------------------------------------------------------------


def test_valid_signature_persists_message(
    app: FastAPI, store: InMemoryInboundMessageStore, clock: FixedClock
) -> None:
    body, headers = signed_post(webhook_payload([text_message("wamid.1", "hola")]))

    with TestClient(app) as client:
        response = client.post(URL, content=body, headers=headers)

    assert response.status_code == 200
    stored = store.get("wamid.1")
    assert stored is not None
    assert stored.sender == "+34600111222"
    assert stored.text == "hola"
    assert stored.profile_name == "Lucía"
    assert stored.received_at == clock.now()


@pytest.mark.parametrize("signature", [None, "sha256=deadbeef", "md5=whatever"])
def test_invalid_signature_is_rejected(
    app: FastAPI, store: InMemoryInboundMessageStore, signature: str | None
) -> None:
    body = json.dumps(webhook_payload([text_message("wamid.1", "hola")])).encode()
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-Hub-Signature-256"] = signature

    response = TestClient(app).post(URL, content=body, headers=headers)

    assert response.status_code == 401
    assert store.get("wamid.1") is None


def test_signature_with_other_secret_is_rejected(
    app: FastAPI, store: InMemoryInboundMessageStore
) -> None:
    body = json.dumps(webhook_payload([text_message("wamid.1", "hola")])).encode()
    headers = {"X-Hub-Signature-256": sign(body, secret="otro")}

    response = TestClient(app).post(URL, content=body, headers=headers)

    assert response.status_code == 401
    assert store.list_unprocessed() == []


def test_invalid_json_with_valid_signature_is_bad_request(app: FastAPI) -> None:
    body = b"not json"

    response = TestClient(app).post(URL, content=body, headers={"X-Hub-Signature-256": sign(body)})

    assert response.status_code == 400


def test_unknown_shape_is_acknowledged(app: FastAPI, store: InMemoryInboundMessageStore) -> None:
    body, headers = signed_post({"object": "page", "entry": "raro"})

    response = TestClient(app).post(URL, content=body, headers=headers)

    assert response.status_code == 200
    assert store.list_unprocessed() == []


def test_duplicate_message_is_processed_once(
    app: FastAPI, store: InMemoryInboundMessageStore, handler: RecordingHandler
) -> None:
    body, headers = signed_post(webhook_payload([text_message("wamid.1", "hola")]))

    with TestClient(app) as client:
        assert client.post(URL, content=body, headers=headers).status_code == 200
        assert client.post(URL, content=body, headers=headers).status_code == 200

    # Al apagar se entregan los lotes pendientes: un solo mensaje, una sola vez.
    assert [[m.external_id for m in b.messages] for b in handler.batches] == [["wamid.1"]]
    stored = store.get("wamid.1")
    assert stored is not None and stored.processed


def test_status_webhook_is_ignored(
    app: FastAPI, store: InMemoryInboundMessageStore, handler: RecordingHandler
) -> None:
    statuses = [
        {"id": "wamid.OUT1", "status": status, "timestamp": "1792663200", "recipient_id": "34600"}
        for status in ("sent", "delivered", "read")
    ]
    body, headers = signed_post(webhook_payload(statuses=statuses))

    with TestClient(app) as client:
        response = client.post(URL, content=body, headers=headers)

    assert response.status_code == 200
    assert store.list_unprocessed() == []
    assert handler.batches == []


def test_other_phone_number_id_is_ignored(app: FastAPI, store: InMemoryInboundMessageStore) -> None:
    payload = webhook_payload([text_message("wamid.1", "hola")], phone_number_id="OTHER")
    body, headers = signed_post(payload)

    response = TestClient(app).post(URL, content=body, headers=headers)

    assert response.status_code == 200
    assert store.get("wamid.1") is None


def test_store_failure_returns_500(
    settings: WhatsAppSettings, handler: RecordingHandler, clock: FixedClock
) -> None:
    class BrokenStore(InMemoryInboundMessageStore):
        def add(self, message):  # type: ignore[no-untyped-def]
            raise ConnectionError("db down")

    app = create_app(settings, store=BrokenStore(), handler=handler, clock=clock)
    body, headers = signed_post(webhook_payload([text_message("wamid.1", "hola")]))

    response = TestClient(app, raise_server_exceptions=False).post(
        URL, content=body, headers=headers
    )

    assert response.status_code == 500


def test_non_text_message_is_persisted_without_text(
    app: FastAPI, store: InMemoryInboundMessageStore
) -> None:
    audio = {
        "from": "34600111222",
        "id": "wamid.A",
        "timestamp": "1792663200",
        "type": "audio",
        "audio": {"id": "media-1"},
    }
    body, headers = signed_post(webhook_payload([audio]))

    with TestClient(app) as client:
        client.post(URL, content=body, headers=headers)

    stored = store.get("wamid.A")
    assert stored is not None
    assert stored.type == "audio"
    assert stored.text is None


# --- Encolado (spec 016) -------------------------------------------------------------


class RecordingQueue:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any], float, str]] = []
        self.fail = fail

    def enqueue(
        self, path: str, payload: dict[str, Any], *, delay_seconds: float, task_id: str
    ) -> bool:
        if self.fail:
            raise ConnectionError("cola caída")
        created = all(task_id != call[3] for call in self.calls)
        self.calls.append((path, payload, delay_seconds, task_id))
        return created


def make_queued_app(
    settings: WhatsAppSettings,
    store: InMemoryInboundMessageStore,
    handler: RecordingHandler,
    clock: FixedClock,
    queue: RecordingQueue,
) -> FastAPI:
    return create_app(
        settings,
        store=store,
        handler=handler,
        clock=clock,
        task_queue=queue,
        tasks_token=SecretStr("tok"),
    )


def test_webhook_enqueues_instead_of_processing(
    settings: WhatsAppSettings,
    store: InMemoryInboundMessageStore,
    handler: RecordingHandler,
    clock: FixedClock,
) -> None:
    queue = RecordingQueue()
    app = make_queued_app(settings, store, handler, clock, queue)
    payload = webhook_payload(
        [
            text_message("wamid.1", "hola", sender="34600111222"),
            text_message("wamid.2", "buenas", sender="5215512345678"),
            text_message("wamid.3", "¿sigues?", sender="34600111222"),
        ]
    )
    body, headers = signed_post(payload)

    with TestClient(app) as client:
        assert client.post(URL, content=body, headers=headers).status_code == 200

    assert handler.batches == []
    assert [(path, payload, delay) for path, payload, delay, _ in queue.calls] == [
        ("/tasks/process-inbound", {"sender": "+34600111222"}, 8),
        ("/tasks/process-inbound", {"sender": "+525512345678"}, 8),
    ]
    assert len(store.list_unprocessed()) == 3


def test_duplicate_still_unprocessed_is_enqueued_again(
    settings: WhatsAppSettings,
    store: InMemoryInboundMessageStore,
    handler: RecordingHandler,
    clock: FixedClock,
) -> None:
    queue = RecordingQueue()
    app = make_queued_app(settings, store, handler, clock, queue)
    body, headers = signed_post(webhook_payload([text_message("wamid.1", "hola")]))
    client = TestClient(app)

    client.post(URL, content=body, headers=headers)
    client.post(URL, content=body, headers=headers)  # reintento de Meta, aún sin procesar
    store.mark_processed(["wamid.1"])
    client.post(URL, content=body, headers=headers)  # ya procesado: se ignora

    assert len(queue.calls) == 2
    assert queue.calls[0][3] == queue.calls[1][3]  # mismo nombre: Cloud Tasks no duplica


def test_queue_failure_returns_500_and_keeps_message(
    settings: WhatsAppSettings,
    store: InMemoryInboundMessageStore,
    handler: RecordingHandler,
    clock: FixedClock,
) -> None:
    app = make_queued_app(settings, store, handler, clock, RecordingQueue(fail=True))
    body, headers = signed_post(webhook_payload([text_message("wamid.1", "hola")]))

    response = TestClient(app, raise_server_exceptions=False).post(
        URL, content=body, headers=headers
    )

    assert response.status_code == 500
    assert [m.external_id for m in store.list_unprocessed()] == ["wamid.1"]


def test_create_app_requires_token_with_external_queue(
    settings: WhatsAppSettings,
    store: InMemoryInboundMessageStore,
    handler: RecordingHandler,
    clock: FixedClock,
) -> None:
    with pytest.raises(ValueError, match="tasks_token"):
        create_app(settings, store=store, handler=handler, clock=clock, task_queue=RecordingQueue())


# --- Agrupación de punta a punta con la cola local ---------------------------------


def make_async_app(
    settings: WhatsAppSettings,
    store: InMemoryInboundMessageStore,
    handler: RecordingHandler,
    clock: FixedClock,
) -> tuple[FastAPI, FakeTimer]:
    timer = FakeTimer(clock)
    app = create_app(settings, store=store, handler=handler, clock=clock, sleep=timer.sleep)
    return app, timer


def async_client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def post_text(client: httpx.AsyncClient, wamid: str, text: str, sender: str) -> None:
    body, headers = signed_post(webhook_payload([text_message(wamid, text, sender=sender)]))
    assert (await client.post(URL, content=body, headers=headers)).status_code == 200


def test_consecutive_messages_are_batched(
    settings: WhatsAppSettings,
    store: InMemoryInboundMessageStore,
    handler: RecordingHandler,
    clock: FixedClock,
) -> None:
    app, timer = make_async_app(settings, store, handler, clock)

    async def scenario() -> None:
        async with async_client(app) as client:
            await post_text(client, "wamid.0", "hola", "34600111222")
            await timer.advance(3)
            await post_text(client, "wamid.1", "soy Lucía", "34600111222")
            await timer.advance(3)
            await post_text(client, "wamid.2", "tengo moto", "34600111222")

            assert handler.batches == []  # 6 s desde el primero: la ventana sigue abierta
            await timer.advance(2)
            await eventually(lambda: len(handler.batches) == 1)
            await timer.advance(30)

    run(scenario())

    [batch] = handler.batches
    assert batch.sender == "+34600111222"
    assert batch.text == "hola\nsoy Lucía\ntengo moto"
    assert store.list_unprocessed() == []


def test_message_in_the_next_window_is_never_lost(
    settings: WhatsAppSettings,
    store: InMemoryInboundMessageStore,
    handler: RecordingHandler,
    clock: FixedClock,
) -> None:
    app, timer = make_async_app(settings, store, handler, clock)

    async def scenario() -> None:
        async with async_client(app) as client:
            await post_text(client, "wamid.0", "hola", "34600111222")
            await timer.advance(8)  # vence la primera ventana
            await eventually(lambda: len(handler.batches) == 1)
            await post_text(client, "wamid.1", "¿sigues?", "34600111222")
            await timer.advance(8)
            await eventually(lambda: len(handler.batches) == 2)

    run(scenario())

    assert [b.text for b in handler.batches] == ["hola", "¿sigues?"]
    assert store.list_unprocessed() == []


def test_messages_from_different_senders_are_separate_batches(
    settings: WhatsAppSettings,
    store: InMemoryInboundMessageStore,
    handler: RecordingHandler,
    clock: FixedClock,
) -> None:
    app, timer = make_async_app(settings, store, handler, clock)
    payload = webhook_payload(
        [
            text_message("wamid.1", "hola", sender="34600111222"),
            text_message("wamid.2", "buenas", sender="5215512345678"),
            text_message("wamid.3", "¿sigues?", sender="34600111222"),
        ]
    )

    async def scenario() -> None:
        async with async_client(app) as client:
            body, headers = signed_post(payload)
            await client.post(URL, content=body, headers=headers)
            await timer.advance(8)
            await eventually(lambda: len(handler.batches) == 2)

    run(scenario())

    by_sender = {b.sender: b.text for b in handler.batches}
    assert by_sender == {"+34600111222": "hola\n¿sigues?", "+525512345678": "buenas"}


def test_failing_handler_leaves_messages_unprocessed(
    settings: WhatsAppSettings,
    store: InMemoryInboundMessageStore,
    clock: FixedClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    handler = RecordingHandler(fail=True)
    app, timer = make_async_app(settings, store, handler, clock)

    async def scenario() -> None:
        async with async_client(app) as client:
            await post_text(client, "wamid.1", "hola", "34600111222")
            await timer.advance(8)
            await eventually(lambda: "boom" in caplog.text)

    run(scenario())

    assert [m.external_id for m in store.list_unprocessed()] == ["wamid.1"]


# --- Configuración -------------------------------------------------------------------


def test_settings_from_env() -> None:
    env = {
        "WHATSAPP_VERIFY_TOKEN": "v",
        "WHATSAPP_APP_SECRET": "s",
        "WHATSAPP_PHONE_NUMBER_ID": "123",
        "WHATSAPP_BATCH_SECONDS": "5",
    }

    settings = WhatsAppSettings.from_env(env)

    assert settings.batch_seconds == 5
    assert settings.phone_number_id == "123"
    assert settings.access_token is None


def test_settings_from_env_requires_secrets() -> None:
    with pytest.raises(ValueError, match="WHATSAPP_APP_SECRET"):
        WhatsAppSettings.from_env({"WHATSAPP_VERIFY_TOKEN": "v"})
