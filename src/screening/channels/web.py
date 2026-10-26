"""Canal del chat web (spec 019): no envía nada afuera.

El mensaje del agente queda en `candidate.messages` (lo guarda el orquestador) y la página de
chat lo lee de ahí por la API. Enviar siempre funciona.
"""

from __future__ import annotations

import logging
from itertools import count

from screening.domain import ChannelName
from screening.ports import OutboundMessage, SendResult

logger = logging.getLogger(__name__)


class WebChatChannel:
    name = ChannelName.WEB

    def __init__(self) -> None:
        self._ids = count(1)

    def send(self, message: OutboundMessage) -> SendResult:
        logger.info("chat web → %s: %s", message.to, message.text[:80])
        return SendResult(ok=True, external_id=f"web-out-{next(self._ids)}")
