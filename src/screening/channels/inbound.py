"""Mensajes entrantes: modelo, persistencia, agrupación y handlers.

La persistencia en Firestore está en `adapters/firestore.py`; aquí, la interfaz y la versión en
memoria. La agrupación en lotes la hace la cola de tareas (spec 016).
"""

from __future__ import annotations

import logging
from datetime import datetime
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

    def list_unprocessed_for(self, sender: str) -> list[InboundMessage]:
        """Sin procesar de un remitente, por consulta indexada (spec 016)."""
        ...


class CandidateBusyError(Exception):
    """El handler no pudo tomar el lease del candidato (spec 015): reintentar más tarde.

    `InboundHandler.handle` la lanza en vez de tragarla; la tarea de la spec 016 responde
    503 y Cloud Tasks reintenta con backoff.
    """


class InboundHandler(Protocol):
    def handle(self, batch: InboundBatch) -> None:
        """Puede lanzar `CandidateBusyError`; cualquier excepción deja el lote sin procesar."""
        ...


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

    def list_unprocessed_for(self, sender: str) -> list[InboundMessage]:
        return [m for m in self.list_unprocessed() if m.sender == sender]


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
