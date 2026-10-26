"""Cliente HTTP de la API del panel: lo que usa la app de Streamlit (spec 013 y 018)."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

import httpx

from screening.crm.interviews import SlotOption
from screening.crm.listing import CandidateRowPage
from screening.crm.metrics import CrmMetrics, StatusCounts
from screening.domain import Candidate, CandidateStatus, Country, Stage
from screening.ports import CandidateOrder

CRM_PATH = "/crm"
DEFAULT_API_URL = "http://localhost:8000"
# Una sola contraseña: entra al panel y es el bearer hacia la API.
PASSWORD_ENV = "PANEL_PASSWORD"
API_URL_ENV = "PANEL_API_URL"


class CrmApiError(Exception):
    """La API respondió con error o no se pudo conectar (`status_code` es None)."""

    def __init__(self, status_code: int | None, detail: str) -> None:
        super().__init__(f"{status_code or 'sin conexión'}: {detail}")
        self.status_code = status_code
        self.detail = detail


class CrmClient:
    def __init__(
        self,
        base_url: str,
        password: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(
            base_url=f"{self.base_url}{CRM_PATH}",
            headers={"Authorization": f"Bearer {password}"},
            transport=transport,
            timeout=timeout,
        )

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] = os.environ, *, transport: httpx.BaseTransport | None = None
    ) -> CrmClient:
        password = env.get(PASSWORD_ENV)
        if not password:
            raise ValueError(f"Falta la variable de entorno {PASSWORD_ENV}")
        return cls(env.get(API_URL_ENV) or DEFAULT_API_URL, password, transport=transport)

    def list_candidates(
        self,
        *,
        statuses: Iterable[CandidateStatus] | None = None,
        country: Country | None = None,
        stage: Stage | None = None,
        order: CandidateOrder | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> CandidateRowPage:
        params: list[tuple[str, str]] = [("status", s.value) for s in statuses or []]
        if country is not None:
            params.append(("country", country.value))
        if stage is not None:
            params.append(("stage", stage.value))
        if order is not None:
            params.append(("order", order))
        if limit is not None:
            params.append(("limit", str(limit)))
        if cursor is not None:
            params.append(("cursor", cursor))
        return CandidateRowPage.model_validate(self._request("GET", "/candidates", params=params))

    def counts(self) -> StatusCounts:
        return StatusCounts.model_validate(self._request("GET", "/counts"))

    def get_candidate(self, candidate_id: str) -> Candidate:
        return Candidate.model_validate(self._request("GET", f"/candidates/{candidate_id}"))

    def change_status(
        self,
        candidate_id: str,
        status: CandidateStatus,
        *,
        reason: str | None = None,
        recruiter: str | None = None,
    ) -> Candidate:
        body = {"status": status.value, "reason": reason, "recruiter": recruiter}
        data = self._request("POST", f"/candidates/{candidate_id}/status", json=body)
        return Candidate.model_validate(data)

    def free_slots(self, candidate_id: str, *, limit: int | None = None) -> list[SlotOption]:
        params = {"limit": str(limit)} if limit is not None else None
        data = self._request("GET", f"/candidates/{candidate_id}/slots", params=params)
        return [SlotOption.model_validate(item) for item in data]

    def book_interview(
        self, candidate_id: str, start: datetime, *, recruiter: str | None = None
    ) -> Candidate:
        body = {"action": "book", "start": start.isoformat(), "recruiter": recruiter}
        return self._change_interview(candidate_id, body)

    def cancel_interview(self, candidate_id: str, *, recruiter: str | None = None) -> Candidate:
        return self._change_interview(candidate_id, {"action": "cancel", "recruiter": recruiter})

    def metrics(self) -> CrmMetrics:
        return CrmMetrics.model_validate(self._request("GET", "/metrics"))

    def _change_interview(self, candidate_id: str, body: dict[str, Any]) -> Candidate:
        data = self._request("POST", f"/candidates/{candidate_id}/interview", json=body)
        return Candidate.model_validate(data)

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise CrmApiError(None, str(e)) from e
        if response.is_error:
            raise CrmApiError(response.status_code, _detail(response))
        return response.json()


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text
    detail = body.get("detail", body) if isinstance(body, dict) else body
    return detail if isinstance(detail, str) else str(detail)
