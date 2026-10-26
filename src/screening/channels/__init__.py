"""Canales de mensajería (WhatsApp, SMS simulado, consola) y mensajes entrantes."""

from screening.channels.console import ConsoleChannel
from screening.channels.inbound import (
    CandidateBusyError,
    EchoInboundHandler,
    InboundBatch,
    InboundHandler,
    InboundMessage,
    InboundMessageStore,
    InMemoryInboundMessageStore,
    LoggingInboundHandler,
    MessageBatcher,
)
from screening.channels.sms import SentSms, SimulatedSmsChannel
from screening.channels.whatsapp import WhatsAppCloudChannel, normalize_whatsapp_phone

__all__ = [
    "CandidateBusyError",
    "ConsoleChannel",
    "EchoInboundHandler",
    "InMemoryInboundMessageStore",
    "InboundBatch",
    "InboundHandler",
    "InboundMessage",
    "InboundMessageStore",
    "LoggingInboundHandler",
    "MessageBatcher",
    "SentSms",
    "SimulatedSmsChannel",
    "WhatsAppCloudChannel",
    "normalize_whatsapp_phone",
]
