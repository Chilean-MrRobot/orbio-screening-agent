"""Cliente HTTP del chat web público (spec 019). No usa la contraseña del panel."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import httpx

from screening.api.web_chat import ChatView
from screening.crm.client import API_URL_ENV, DEFAULT_API_URL, CrmApiError, _detail
from screening.domain import Country

# Un turno incluye extracción y respuesta del LLM: puede tardar varios segundos.
TURN_TIMEOUT_SECONDS = 90.0


class ChatClient:
    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = TURN_TIMEOUT_SECONDS,
    ) -> None:
        self._http = httpx.Client(
            base_url=f"{base_url.rstrip('/')}/chat", transport=transport, timeout=timeout
        )

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] = os.environ, *, transport: httpx.BaseTransport | None = None
    ) -> ChatClient:
        return cls(env.get(API_URL_ENV) or DEFAULT_API_URL, transport=transport)

    def start(
        self, *, full_name: str, country: Country, phone: str | None = None, city: str | None = None
    ) -> ChatView:
        body = {"full_name": full_name, "country": country.value, "phone": phone, "city": city}
        return ChatView.model_validate(self._request("POST", "/start", json=body))

    def get(self, candidate_id: str) -> ChatView:
        return ChatView.model_validate(self._request("GET", f"/{candidate_id}"))

    def send(self, candidate_id: str, text: str) -> ChatView:
        data = self._request("POST", f"/{candidate_id}/messages", json={"text": text})
        return ChatView.model_validate(data)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise CrmApiError(None, str(e)) from e
        if response.is_error:
            raise CrmApiError(response.status_code, _detail(response))
        return response.json()
