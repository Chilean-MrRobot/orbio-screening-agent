"""Mensajes entrantes: modelo, persistencia, agrupación y handlers.

La persistencia en Firestore llega en la ola 2; aquí están la interfaz y la versión en memoria.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from pydantic import BaseModel

from screening.domain import ChannelName, Message, MessageRole
from screening.ports import MessageChannel, OutboundMessage

logger = logging.getLogger(__name__)


class InboundMessage(BaseModel):
    external_id: str
    sender: str
    text: str | None
    type: str
    profile_name: str | None = None
    sent_at: datetime
    received_at: datetime
    processed: bool = False

    def to_domain_message(self) -> Message:
        return Message(
            role=MessageRole.CANDIDATE,
            text=self.text or "",
            channel=ChannelName.WHATSAPP,
            created_at=self.received_at,
            external_id=self.external_id,
        )


class InboundBatch(BaseModel):
    """Mensajes seguidos de un mismo remitente, entregados juntos al handler."""

    sender: str
    messages: list[InboundMessage]

    @property
    def text(self) -> str:
        return "\n".join(m.text for m in self.messages if m.text)


class InboundMessageStore(Protocol):
    def add(self, message: InboundMessage) -> bool:
        """Persiste el mensaje. Devuelve `False` si su ID ya existía (duplicado)."""
        ...

    def mark_processed(self, external_ids: list[str]) -> None: ...

    def get(self, external_id: str) -> InboundMessage | None: ...

    def list_unprocessed(self) -> list[InboundMessage]: ...


class InboundHandler(Protocol):
    def handle(self, batch: InboundBatch) -> None: ...


class CandidateBusyError(Exception):
    """El handler no pudo tomar al candidato; el lote no se procesó y hay que reintentarlo."""


class InMemoryInboundMessageStore:
    """Guarda copias para comportarse como una base de datos real."""

    def __init__(self) -> None:
        self._items: dict[str, InboundMessage] = {}

    def add(self, message: InboundMessage) -> bool:
        if message.external_id in self._items:
            return False
        self._items[message.external_id] = message.model_copy(deep=True)
        return True

    def mark_processed(self, external_ids: list[str]) -> None:
        for external_id in external_ids:
            if external_id in self._items:
                self._items[external_id].processed = True

    def get(self, external_id: str) -> InboundMessage | None:
        message = self._items.get(external_id)
        return message.model_copy(deep=True) if message else None

    def list_unprocessed(self) -> list[InboundMessage]:
        return [m.model_copy(deep=True) for m in self._items.values() if not m.processed]


@dataclass
class _Pending:
    deadline: datetime
    messages: list[InboundMessage] = field(default_factory=list)


class MessageBatcher:
    """Ventana deslizante por remitente: cada mensaje nuevo reinicia el plazo.

    Es puro y síncrono (el tiempo entra como argumento); quien decide cuándo despertar es
    el despachador de la API.
    """

    def __init__(self, window_seconds: float) -> None:
        self._window = timedelta(seconds=window_seconds)
        self._pending: dict[str, _Pending] = {}

    def add(self, message: InboundMessage, now: datetime) -> None:
        pending = self._pending.setdefault(message.sender, _Pending(deadline=now))
        pending.messages.append(message)
        pending.deadline = now + self._window

    def seconds_until_due(self, sender: str, now: datetime) -> float | None:
        pending = self._pending.get(sender)
        if pending is None:
            return None
        return max(0.0, (pending.deadline - now).total_seconds())

    def pop(self, sender: str) -> InboundBatch | None:
        pending = self._pending.pop(sender, None)
        if pending is None:
            return None
        return InboundBatch(sender=sender, messages=pending.messages)

    def pop_all(self) -> list[InboundBatch]:
        return [batch for sender in list(self._pending) if (batch := self.pop(sender))]


class LoggingInboundHandler:
    """Handler de prueba: solo registra el lote."""

    def handle(self, batch: InboundBatch) -> None:
        logger.info("Lote de %s (%d mensajes): %s", batch.sender, len(batch.messages), batch.text)


class EchoInboundHandler:
    """Handler de prueba para el demo: responde con el texto recibido.

    Sirve para comprobar el circuito completo (webhook → lote → envío) antes de la ola 2.
    """

    def __init__(self, channel: MessageChannel) -> None:
        self._channel = channel

    def handle(self, batch: InboundBatch) -> None:
        text = batch.text or "(mensaje sin texto)"
        result = self._channel.send(OutboundMessage(to=batch.sender, text=f"Recibí: {text}"))
        if not result.ok:
            logger.warning("Eco a %s falló: %s", batch.sender, result.error)
