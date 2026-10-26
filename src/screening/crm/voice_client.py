"""Cliente HTTP del agente de voz (spec 020). Manda audio a la API; nunca ve la key."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import httpx

from screening.api.voice import VoiceStatus, VoiceTurn
from screening.crm.chat_client import TURN_TIMEOUT_SECONDS
from screening.crm.client import API_URL_ENV, DEFAULT_API_URL, CrmApiError, _detail
from screening.domain import Country


class VoiceClient:
    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = TURN_TIMEOUT_SECONDS,
    ) -> None:
        self._http = httpx.Client(
            base_url=f"{base_url.rstrip('/')}/voice", transport=transport, timeout=timeout
        )

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] = os.environ, *, transport: httpx.BaseTransport | None = None
    ) -> VoiceClient:
        return cls(env.get(API_URL_ENV) or DEFAULT_API_URL, transport=transport)

    def enabled(self) -> bool:
        """Si la API no responde, la voz se considera deshabilitada."""
        try:
            return VoiceStatus.model_validate(self._request("GET", "/status")).enabled
        except CrmApiError:
            return False

    def start(
        self,
        *,
        full_name: str,
        country: Country | str,
        phone: str | None = None,
        city: str | None = None,
    ) -> VoiceTurn:
        body = {
            "full_name": full_name,
            "country": Country(country).value,
            "phone": phone,
            "city": city,
        }
        return VoiceTurn.model_validate(self._request("POST", "/start", json=body))

    def turn(self, candidate_id: str, audio: bytes, mime_type: str) -> VoiceTurn:
        files = {"audio": ("respuesta.wav", audio, mime_type)}
        return VoiceTurn.model_validate(self._request("POST", f"/{candidate_id}/turn", files=files))

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise CrmApiError(None, str(e)) from e
        if response.is_error:
            raise CrmApiError(response.status_code, _detail(response))
        return response.json()
