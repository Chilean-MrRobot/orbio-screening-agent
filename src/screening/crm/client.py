"""Cliente HTTP de la API del CRM: lo que usa la app de Streamlit (spec 013)."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from typing import Any

import httpx

from screening.crm.listing import CandidateRow
from screening.crm.metrics import CrmMetrics
from screening.domain import Candidate, CandidateStatus, Country, Stage

CRM_PATH = "/crm"
DEFAULT_API_URL = "http://localhost:8000"


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
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(
            base_url=f"{self.base_url}{CRM_PATH}",
            headers={"Authorization": f"Bearer {token}"},
            transport=transport,
            timeout=timeout,
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> CrmClient:
        token = env.get("CRM_API_TOKEN")
        if not token:
            raise ValueError("Falta la variable de entorno CRM_API_TOKEN")
        return cls(env.get("CRM_API_URL") or DEFAULT_API_URL, token)

    def list_candidates(
        self,
        *,
        statuses: Iterable[CandidateStatus] | None = None,
        country: Country | None = None,
        stage: Stage | None = None,
    ) -> list[CandidateRow]:
        params: list[tuple[str, str]] = [("status", s.value) for s in statuses or []]
        if country is not None:
            params.append(("country", country.value))
        if stage is not None:
            params.append(("stage", stage.value))
        data = self._request("GET", "/candidates", params=params)
        return [CandidateRow.model_validate(item) for item in data]

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

    def metrics(self) -> CrmMetrics:
        return CrmMetrics.model_validate(self._request("GET", "/metrics"))

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
