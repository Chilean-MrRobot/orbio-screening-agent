"""SMS simulado: no envía nada, solo registra lo que se habría enviado."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from screening.clock import Clock
from screening.domain import ChannelName
from screening.ports import OutboundMessage, SendResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SentSms:
    to: str
    text: str
    template: str | None
    at: datetime


class SimulatedSmsChannel:
    name = ChannelName.SMS

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self.sent: list[SentSms] = []

    def send(self, message: OutboundMessage) -> SendResult:
        sms = SentSms(
            to=message.to, text=message.text, template=message.template, at=self._clock.now()
        )
        self.sent.append(sms)
        logger.info("SMS simulado a %s: %s", sms.to, sms.text)
        return SendResult(ok=True, external_id=f"sms-sim-{len(self.sent)}")
