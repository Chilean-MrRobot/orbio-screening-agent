"""Canales de mensajería (WhatsApp, chat web, SMS simulado, consola) y mensajes entrantes."""

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
)
from screening.channels.sms import SentSms, SimulatedSmsChannel
from screening.channels.web import WebChatChannel
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
    "SentSms",
    "SimulatedSmsChannel",
    "WebChatChannel",
    "WhatsAppCloudChannel",
    "normalize_whatsapp_phone",
]
