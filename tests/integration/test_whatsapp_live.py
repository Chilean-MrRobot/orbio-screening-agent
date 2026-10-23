"""Envío real por WhatsApp Cloud API. Se salta si faltan las credenciales."""

from __future__ import annotations

import os

import pytest

from screening.channels import WhatsAppCloudChannel
from screening.ports import OutboundMessage

REQUIRED = ("WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_TEST_RECIPIENT")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        any(not os.environ.get(name) for name in REQUIRED),
        reason=f"faltan credenciales: {', '.join(REQUIRED)}",
    ),
]


def test_sends_hello_world_template() -> None:
    # `hello_world` viene aprobada en el número de prueba y se puede enviar fuera de la
    # ventana de 24 h, así que no depende de que el destinatario haya escrito antes.
    channel = WhatsAppCloudChannel(
        os.environ["WHATSAPP_ACCESS_TOKEN"],
        os.environ["WHATSAPP_PHONE_NUMBER_ID"],
        api_version=os.environ.get("WHATSAPP_GRAPH_VERSION", "v23.0"),
        template_language="en_US",
    )

    result = channel.send(
        OutboundMessage(to=os.environ["WHATSAPP_TEST_RECIPIENT"], text="", template="hello_world")
    )

    assert result.ok, result.error
    assert result.external_id
