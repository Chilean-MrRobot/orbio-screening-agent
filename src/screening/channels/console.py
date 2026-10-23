"""Canal de consola para desarrollo local."""

from __future__ import annotations

import sys
from itertools import count
from typing import TextIO

from screening.domain import ChannelName
from screening.ports import OutboundMessage, SendResult


class ConsoleChannel:
    name = ChannelName.CONSOLE

    def __init__(self, out: TextIO = sys.stdout) -> None:
        self._out = out
        self._ids = count(1)

    def send(self, message: OutboundMessage) -> SendResult:
        print(f"[agente → {message.to}] {message.text}", file=self._out, flush=True)
        return SendResult(ok=True, external_id=f"console-{next(self._ids)}")
