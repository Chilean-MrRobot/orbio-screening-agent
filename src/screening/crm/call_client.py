"""Cliente HTTP de la llamada en tiempo real (spec 022). Nunca ve la key de ElevenLabs."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import httpx

from screening.api.voice_call import CallSession, CallStatus
from screening.crm.client import API_URL_ENV, DEFAULT_API_URL, CrmApiError, _detail
from screening.domain import Country

# Crear la postulación incluye el saludo; pedir el token es una llamada corta a ElevenLabs.
TIMEOUT_SECONDS = 60.0


class CallClient:
    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = TIMEOUT_SECONDS,
    ) -> None:
        self._http = httpx.Client(
            base_url=f"{base_url.rstrip('/')}/voice/call", transport=transport, timeout=timeout
        )

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] = os.environ, *, transport: httpx.BaseTransport | None = None
    ) -> CallClient:
        return cls(env.get(API_URL_ENV) or DEFAULT_API_URL, transport=transport)

    def enabled(self) -> bool:
        """Si la API no responde, la llamada se considera deshabilitada."""
        try:
            return CallStatus.model_validate(self._request("GET", "/status")).enabled
        except CrmApiError:
            return False

    def start(
        self,
        *,
        full_name: str,
        country: Country | str,
        phone: str | None = None,
        city: str | None = None,
    ) -> CallSession:
        body = {
            "full_name": full_name,
            "country": Country(country).value,
            "phone": phone,
            "city": city,
        }
        return CallSession.model_validate(self._request("POST", "/start", json=body))

    def token(self, candidate_id: str) -> CallSession:
        return CallSession.model_validate(self._request("POST", f"/{candidate_id}/token"))

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise CrmApiError(None, str(e)) from e
        if response.is_error:
            raise CrmApiError(response.status_code, _detail(response))
        return response.json()
