"""Webhook de WhatsApp Cloud API: verificación, firma, persistencia, deduplicación y encolado."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from screening.api.inbound_tasks import InboundScheduler
from screening.api.settings import WhatsAppSettings
from screening.channels import InboundMessage, InboundMessageStore, normalize_whatsapp_phone
from screening.clock import Clock

logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/webhooks/whatsapp"
SIGNATURE_HEADER = "X-Hub-Signature-256"


# --- Payload de Meta (solo los campos que usamos) ----------------------------------


class _Payload(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Text(_Payload):
    body: str


class _Button(_Payload):
    text: str


class _Reply(_Payload):
    title: str


class _Interactive(_Payload):
    button_reply: _Reply | None = None
    list_reply: _Reply | None = None


class _WaMessage(_Payload):
    sender: str = Field(alias="from")
    id: str
    timestamp: str
    type: str
    text: _Text | None = None
    button: _Button | None = None
    interactive: _Interactive | None = None

    def body(self) -> str | None:
        if self.text:
            return self.text.body
        if self.button:
            return self.button.text
        if self.interactive:
            reply = self.interactive.button_reply or self.interactive.list_reply
            return reply.title if reply else None
        return None


class _Profile(_Payload):
    name: str | None = None


class _Contact(_Payload):
    wa_id: str
    profile: _Profile | None = None


class _Metadata(_Payload):
    phone_number_id: str


class _Value(_Payload):
    metadata: _Metadata | None = None
    contacts: list[_Contact] = Field(default_factory=list)
    messages: list[_WaMessage] = Field(default_factory=list)


class _Change(_Payload):
    value: _Value


class _Entry(_Payload):
    changes: list[_Change] = Field(default_factory=list)


class WebhookPayload(_Payload):
    entry: list[_Entry] = Field(default_factory=list)


# --- Lógica -------------------------------------------------------------------------


def is_valid_signature(body: bytes, header: str | None, app_secret: str) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def extract_messages(
    payload: WebhookPayload, phone_number_id: str | None, received_at: datetime
) -> list[InboundMessage]:
    """Mensajes entrantes del payload. Los webhooks de estado no traen `messages`."""
    result: list[InboundMessage] = []
    for entry in payload.entry:
        for change in entry.changes:
            value = change.value
            if (
                phone_number_id
                and value.metadata
                and value.metadata.phone_number_id != phone_number_id
            ):
                continue
            names = {c.wa_id: c.profile.name for c in value.contacts if c.profile}
            for message in value.messages:
                result.append(
                    InboundMessage(
                        external_id=message.id,
                        sender=normalize_whatsapp_phone(message.sender),
                        text=message.body(),
                        type=message.type,
                        profile_name=names.get(message.sender),
                        sent_at=datetime.fromtimestamp(int(message.timestamp), UTC),
                        received_at=received_at,
                    )
                )
    return result


def _still_pending(store: InboundMessageStore, external_id: str) -> bool:
    """Un duplicado aún sin procesar se vuelve a encolar: cubre un encolado que falló."""
    stored = store.get(external_id)
    return stored is not None and not stored.processed


def build_webhook_router(
    settings: WhatsAppSettings,
    store: InboundMessageStore,
    scheduler: InboundScheduler,
    clock: Clock,
) -> APIRouter:
    router = APIRouter()

    @router.get(WEBHOOK_PATH)
    def verify(request: Request) -> Response:
        params = request.query_params
        if (
            params.get("hub.mode") == "subscribe"
            and params.get("hub.verify_token") == settings.verify_token.get_secret_value()
        ):
            return PlainTextResponse(params.get("hub.challenge", ""))
        return PlainTextResponse("forbidden", status_code=403)

    @router.post(WEBHOOK_PATH)
    async def receive(request: Request) -> Response:
        body = await request.body()
        signature = request.headers.get(SIGNATURE_HEADER)
        if not is_valid_signature(body, signature, settings.app_secret.get_secret_value()):
            logger.warning("Webhook de WhatsApp con firma inválida")
            return JSONResponse({"error": "invalid signature"}, status_code=401)

        try:
            data = json.loads(body)
        except ValueError:
            return JSONResponse({"error": "invalid json"}, status_code=400)
        try:
            payload = WebhookPayload.model_validate(data)
        except ValidationError:
            logger.info("Webhook de WhatsApp con forma desconocida; se ignora")
            return JSONResponse({"status": "ignored"})

        senders: list[str] = []
        for message in extract_messages(payload, settings.phone_number_id, clock.now()):
            # Persistir antes de encolar; `add` devuelve False si el wamid ya se recibió.
            if store.add(message) or _still_pending(store, message.external_id):
                if message.sender not in senders:
                    senders.append(message.sender)
            else:
                logger.info("Mensaje duplicado %s ignorado", message.external_id)
        # Una tarea por remitente y ventana (spec 016). Si la cola falla, el 500 hace que Meta
        # reintente; el mensaje ya está persistido y el duplicado sin procesar se reencola.
        for sender in senders:
            scheduler.schedule(sender)
        return JSONResponse({"status": "ok"})

    return router
