from __future__ import annotations

from datetime import datetime

import pytest

from screening.channels import (
    InboundBatch,
    InboundMessage,
    InMemoryInboundMessageStore,
    normalize_whatsapp_phone,
)
from screening.clock import FixedClock
from screening.domain import ChannelName, MessageRole


def make_message(
    external_id: str, at: datetime, sender: str = "+34600111222", text: str | None = "hola"
) -> InboundMessage:
    return InboundMessage(
        external_id=external_id,
        sender=sender,
        text=text,
        type="text" if text is not None else "audio",
        sent_at=at,
        received_at=at,
    )


@pytest.mark.parametrize(
    ("wa_id", "expected"),
    [
        ("34600111222", "+34600111222"),
        ("+34600111222", "+34600111222"),
        ("5215512345678", "+525512345678"),
        ("525512345678", "+525512345678"),
    ],
)
def test_normalize_whatsapp_phone(wa_id: str, expected: str) -> None:
    assert normalize_whatsapp_phone(wa_id) == expected


def test_store_deduplicates_by_external_id(clock: FixedClock) -> None:
    store = InMemoryInboundMessageStore()

    assert store.add(make_message("wamid.1", clock.now()))
    assert not store.add(make_message("wamid.1", clock.now(), text="otro texto"))
    stored = store.get("wamid.1")
    assert stored is not None and stored.text == "hola"


def test_store_tracks_processed(clock: FixedClock) -> None:
    store = InMemoryInboundMessageStore()
    store.add(make_message("wamid.1", clock.now()))
    store.add(make_message("wamid.2", clock.now()))

    store.mark_processed(["wamid.1"])

    assert [m.external_id for m in store.list_unprocessed()] == ["wamid.2"]
    stored = store.get("wamid.1")
    assert stored is not None and stored.processed


def test_store_returns_copies(clock: FixedClock) -> None:
    store = InMemoryInboundMessageStore()
    message = make_message("wamid.1", clock.now())
    store.add(message)

    message.text = "modificado"

    stored = store.get("wamid.1")
    assert stored is not None and stored.text == "hola"


def test_to_domain_message(clock: FixedClock) -> None:
    message = make_message("wamid.1", clock.now()).to_domain_message()

    assert message.role == MessageRole.CANDIDATE
    assert message.channel == ChannelName.WHATSAPP
    assert message.external_id == "wamid.1"
    assert message.text == "hola"
    assert message.created_at == clock.now()


def test_store_lists_unprocessed_by_sender(clock: FixedClock) -> None:
    store = InMemoryInboundMessageStore()
    store.add(make_message("wamid.1", clock.now()))
    store.add(make_message("wamid.2", clock.now(), sender="+525512345678"))
    store.add(make_message("wamid.3", clock.now()))
    store.mark_processed(["wamid.1"])

    pending = store.list_unprocessed_for("+34600111222")

    assert [m.external_id for m in pending] == ["wamid.3"]
    assert store.list_unprocessed_for("+34000000000") == []


def test_batch_text_skips_non_text_messages(clock: FixedClock) -> None:
    batch = InboundBatch(
        sender="+34600111222",
        messages=[
            make_message("wamid.1", clock.now(), text="hola"),
            make_message("wamid.2", clock.now(), text=None),
        ],
    )

    assert batch.text == "hola"
