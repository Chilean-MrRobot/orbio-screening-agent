from __future__ import annotations

import io
import json
import logging

import httpx
import pytest

from screening.channels import ConsoleChannel, SimulatedSmsChannel, WhatsAppCloudChannel
from screening.clock import FixedClock
from screening.domain import ChannelName
from screening.ports import OutboundMessage


def make_channel(handler, **kwargs) -> WhatsAppCloudChannel:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return WhatsAppCloudChannel("token-123", "PHONE_ID", client=client, **kwargs)


def ok_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"messages": [{"id": "wamid.OUT1"}]})


def test_whatsapp_sends_text_message() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return ok_response(request)

    channel = make_channel(handler)
    result = channel.send(OutboundMessage(to="+34600111222", text="Hola Lucía"))

    assert result.ok
    assert result.external_id == "wamid.OUT1"
    assert channel.name == ChannelName.WHATSAPP
    request = requests[0]
    assert request.url == "https://graph.facebook.com/v23.0/PHONE_ID/messages"
    assert request.headers["Authorization"] == "Bearer token-123"
    assert json.loads(request.content) == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": "34600111222",
        "type": "text",
        "text": {"body": "Hola Lucía", "preview_url": False},
    }


def test_whatsapp_sends_template_with_named_params() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return ok_response(request)

    channel = make_channel(handler, template_language="es_MX")
    message = OutboundMessage(
        to="+5215512345678",
        text="fallback",
        template="reminder_never_responded",
        template_params={"first_name": "Ana"},
    )
    result = channel.send(message)

    assert result.ok
    body = json.loads(requests[0].content)
    assert body["type"] == "template"
    assert body["template"] == {
        "name": "reminder_never_responded",
        "language": {"code": "es_MX"},
        "components": [
            {
                "type": "body",
                "parameters": [{"type": "text", "parameter_name": "first_name", "text": "Ana"}],
            }
        ],
    }


def test_whatsapp_template_without_params_has_no_components() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return ok_response(request)

    make_channel(handler).send(OutboundMessage(to="+34600111222", text="", template="hello_world"))

    assert "components" not in json.loads(requests[0].content)["template"]


def test_whatsapp_http_error_returns_not_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": "Recipient phone number not in allowed list",
                    "code": 131030,
                }
            },
        )

    result = make_channel(handler).send(OutboundMessage(to="+34600111222", text="Hola"))

    assert not result.ok
    assert result.external_id is None
    assert result.error == "HTTP 400: (#131030) Recipient phone number not in allowed list"


def test_whatsapp_http_error_without_json_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="Bad gateway")

    result = make_channel(handler).send(OutboundMessage(to="+34600111222", text="Hola"))

    assert not result.ok
    assert result.error == "HTTP 502: Bad gateway"


def test_whatsapp_network_error_returns_not_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    result = make_channel(handler).send(OutboundMessage(to="+34600111222", text="Hola"))

    assert not result.ok
    assert result.error is not None
    assert "ConnectTimeout" in result.error


def test_simulated_sms_records_and_logs(
    clock: FixedClock, caplog: pytest.LogCaptureFixture
) -> None:
    channel = SimulatedSmsChannel(clock)

    with caplog.at_level(logging.INFO, logger="screening.channels.sms"):
        first = channel.send(
            OutboundMessage(to="+34600111222", text="Tu postulación sigue abierta")
        )
        clock.advance(hours=1)
        second = channel.send(OutboundMessage(to="+34600333444", text="Otro"))

    assert channel.name == ChannelName.SMS
    assert first.ok and first.external_id == "sms-sim-1"
    assert second.external_id == "sms-sim-2"
    assert [(s.to, s.text) for s in channel.sent] == [
        ("+34600111222", "Tu postulación sigue abierta"),
        ("+34600333444", "Otro"),
    ]
    assert channel.sent[1].at == clock.now()
    assert "+34600111222" in caplog.text
    assert "Tu postulación sigue abierta" in caplog.text


def test_console_channel_writes_to_stream() -> None:
    out = io.StringIO()
    channel = ConsoleChannel(out)

    result = channel.send(OutboundMessage(to="+34600111222", text="Hola"))

    assert result.ok
    assert channel.name == ChannelName.CONSOLE
    assert out.getvalue() == "[agente → +34600111222] Hola\n"
