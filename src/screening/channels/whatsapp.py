"""Envío por WhatsApp Cloud API (Graph API de Meta)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from screening.domain import ChannelName
from screening.ports import OutboundMessage, SendResult

logger = logging.getLogger(__name__)

GRAPH_URL = "https://graph.facebook.com"
DEFAULT_API_VERSION = "v23.0"


def normalize_whatsapp_phone(wa_id: str) -> str:
    """Convierte el `wa_id` de Meta (solo dígitos) a E.164.

    WhatsApp reporta los móviles de México como `521` + 10 dígitos; la ficha los guarda
    como `+52` + 10 dígitos (el "1" dejó de usarse en 2019).
    """
    digits = wa_id.lstrip("+")
    if digits.startswith("521") and len(digits) == 13:
        digits = "52" + digits[3:]
    return "+" + digits


class WhatsAppCloudChannel:
    name = ChannelName.WHATSAPP

    def __init__(
        self,
        access_token: str,
        phone_number_id: str,
        *,
        client: httpx.Client | None = None,
        api_version: str = DEFAULT_API_VERSION,
        template_language: str = "es",
    ) -> None:
        self._url = f"{GRAPH_URL}/{api_version}/{phone_number_id}/messages"
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._client = client or httpx.Client(timeout=10.0)
        # `OutboundMessage` no trae idioma: se fija por canal (ver spec 005).
        self._template_language = template_language

    def send(self, message: OutboundMessage) -> SendResult:
        try:
            response = self._client.post(
                self._url, json=self._build_body(message), headers=self._headers
            )
        except httpx.HTTPError as exc:
            logger.warning("WhatsApp: error de red enviando a %s: %r", message.to, exc)
            return SendResult(ok=False, error=f"{type(exc).__name__}: {exc}")

        if not response.is_success:
            error = f"HTTP {response.status_code}: {_describe_error(response)}"
            logger.warning("WhatsApp: envío a %s rechazado: %s", message.to, error)
            return SendResult(ok=False, error=error)

        try:
            external_id = response.json()["messages"][0]["id"]
        except (ValueError, KeyError, IndexError, TypeError):
            external_id = None
        return SendResult(ok=True, external_id=external_id)

    def _build_body(self, message: OutboundMessage) -> dict[str, Any]:
        body: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": message.to.lstrip("+"),
        }
        if message.template is None:
            body["type"] = "text"
            body["text"] = {"body": message.text, "preview_url": False}
            return body

        template: dict[str, Any] = {
            "name": message.template,
            "language": {"code": self._template_language},
        }
        if message.template_params:
            template["components"] = [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "parameter_name": key, "text": value}
                        for key, value in message.template_params.items()
                    ],
                }
            ]
        body["type"] = "template"
        body["template"] = template
        return body


def _describe_error(response: httpx.Response) -> str:
    try:
        error = response.json()["error"]
        return f"(#{error['code']}) {error['message']}"
    except (ValueError, KeyError, TypeError):
        return response.text[:200]
