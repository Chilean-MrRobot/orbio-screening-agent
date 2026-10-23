from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest

from screening.crm.client import CrmApiError, CrmClient
from screening.crm.listing import to_row
from screening.crm.metrics import compute_metrics
from screening.domain import CandidateStatus, Country
from tests.unit.crm.conftest import T0, make_candidate

BASE = "http://api.test"


class Recorder:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.response


def client_for(response: httpx.Response) -> tuple[CrmClient, Recorder]:
    recorder = Recorder(response)
    return CrmClient(BASE, "tok", transport=httpx.MockTransport(recorder)), recorder


def test_list_candidates_sends_token_and_filters() -> None:
    candidate = make_candidate(status=CandidateStatus.QUALIFIED, score=80)
    payload = [to_row(candidate).model_dump(mode="json")]
    client, recorder = client_for(httpx.Response(200, json=payload))

    rows = client.list_candidates(
        statuses=[CandidateStatus.QUALIFIED, CandidateStatus.INTERVIEW], country=Country.MX
    )

    request = recorder.requests[0]
    assert request.headers["Authorization"] == "Bearer tok"
    assert request.url.path == "/crm/candidates"
    assert request.url.params.get_list("status") == ["qualified", "interview"]
    assert request.url.params["country"] == "MX"
    assert "stage" not in request.url.params
    assert rows[0].id == candidate.id
    assert rows[0].score == 80


def test_get_candidate_parses_the_full_record() -> None:
    candidate = make_candidate(agent_messages=1)
    client, recorder = client_for(httpx.Response(200, json=candidate.model_dump(mode="json")))

    result = client.get_candidate(candidate.id)

    assert recorder.requests[0].url.path == f"/crm/candidates/{candidate.id}"
    assert result == candidate


def test_change_status_posts_the_body() -> None:
    candidate = make_candidate(status=CandidateStatus.INTERVIEW)
    client, recorder = client_for(httpx.Response(200, json=candidate.model_dump(mode="json")))

    result = client.change_status(
        candidate.id, CandidateStatus.DISQUALIFIED, reason="No se presentó", recruiter="Marta"
    )

    request = recorder.requests[0]
    assert request.method == "POST"
    assert request.url.path == f"/crm/candidates/{candidate.id}/status"
    assert json.loads(request.content) == {
        "status": "disqualified",
        "reason": "No se presentó",
        "recruiter": "Marta",
    }
    assert result.status == CandidateStatus.INTERVIEW


def test_metrics() -> None:
    metrics = compute_metrics([make_candidate()], T0, timedelta(hours=1))
    client, _ = client_for(httpx.Response(200, json=metrics.model_dump(mode="json")))

    assert client.metrics() == metrics


@pytest.mark.parametrize(
    ("response", "detail"),
    [
        (httpx.Response(401, json={"detail": "invalid token"}), "invalid token"),
        (httpx.Response(409, json={"detail": "no se puede"}), "no se puede"),
        (httpx.Response(500, text="boom"), "boom"),
    ],
)
def test_errors_become_crm_api_error(response: httpx.Response, detail: str) -> None:
    client, _ = client_for(response)

    with pytest.raises(CrmApiError) as error:
        client.metrics()

    assert error.value.status_code == response.status_code
    assert detail in error.value.detail


def test_connection_errors_become_crm_api_error() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = CrmClient(BASE, "tok", transport=httpx.MockTransport(fail))

    with pytest.raises(CrmApiError) as error:
        client.list_candidates()

    assert error.value.status_code is None


def test_from_env() -> None:
    client = CrmClient.from_env({"CRM_API_URL": "http://x:8000/", "CRM_API_TOKEN": "t"})

    assert client.base_url == "http://x:8000"

    with pytest.raises(ValueError, match="CRM_API_TOKEN"):
        CrmClient.from_env({"CRM_API_URL": "http://x"})
