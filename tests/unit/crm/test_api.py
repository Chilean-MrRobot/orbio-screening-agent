from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from screening.adapters.memory import InMemoryCandidateRepository
from screening.api import WhatsAppSettings, create_app
from screening.api.crm import CRM_PREFIX, build_crm_router, build_crm_router_from_env
from screening.channels import InMemoryInboundMessageStore, LoggingInboundHandler
from screening.clock import FixedClock
from screening.domain import CandidateStatus, Country, Stage
from tests.unit.crm.conftest import T0, make_candidate, usage

TOKEN = "s3cret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def crm_clock() -> FixedClock:
    return FixedClock(T0 + timedelta(hours=1))


@pytest.fixture
def client(repository: InMemoryCandidateRepository, crm_clock: FixedClock) -> TestClient:
    app = FastAPI()
    app.include_router(build_crm_router(repository, crm_clock, TOKEN))
    return TestClient(app)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/crm/candidates"),
        ("get", "/crm/candidates/x"),
        ("post", "/crm/candidates/x/status"),
        ("get", "/crm/metrics"),
    ],
)
@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}]
)
def test_every_route_requires_the_token(
    client: TestClient, method: str, path: str, headers: dict[str, str]
) -> None:
    response = client.request(method, path, headers=headers, json={"status": "interview"})

    assert response.status_code == 401


def test_lists_sorted_by_score(client: TestClient, repository: InMemoryCandidateRepository) -> None:
    low = make_candidate(status=CandidateStatus.QUALIFIED, score=50)
    high = make_candidate(status=CandidateStatus.QUALIFIED, score=95)
    pending = make_candidate(status=CandidateStatus.IN_SCREENING)
    for c in (low, pending, high):
        repository.save(c)

    response = client.get("/crm/candidates", headers=AUTH)

    assert response.status_code == 200
    rows = response.json()
    assert [r["id"] for r in rows] == [high.id, low.id, pending.id]
    assert rows[0]["score"] == 95
    assert rows[0]["status"] == "qualified"


def test_lists_with_filters(client: TestClient, repository: InMemoryCandidateRepository) -> None:
    mx = make_candidate(status=CandidateStatus.QUALIFIED, country=Country.MX, stage=Stage.DONE)
    mx_interview = make_candidate(
        status=CandidateStatus.INTERVIEW, country=Country.MX, stage=Stage.DONE
    )
    es = make_candidate(status=CandidateStatus.QUALIFIED, stage=Stage.DONE)
    screening = make_candidate(status=CandidateStatus.IN_SCREENING, stage=Stage.KNOCKOUT)
    for c in (mx, mx_interview, es, screening):
        repository.save(c)

    by_status = client.get(
        "/crm/candidates",
        params=[("status", "qualified"), ("status", "interview"), ("country", "MX")],
        headers=AUTH,
    )
    by_stage = client.get("/crm/candidates", params={"stage": "knockout"}, headers=AUTH)
    invalid = client.get("/crm/candidates", params={"status": "bogus"}, headers=AUTH)

    assert {r["id"] for r in by_status.json()} == {mx.id, mx_interview.id}
    assert [r["id"] for r in by_stage.json()] == [screening.id]
    assert invalid.status_code == 422


def test_gets_the_full_candidate(
    client: TestClient, repository: InMemoryCandidateRepository
) -> None:
    candidate = make_candidate(agent_messages=1, candidate_messages=1, usage=[usage()])
    repository.save(candidate)

    response = client.get(f"/crm/candidates/{candidate.id}", headers=AUTH)
    missing = client.get("/crm/candidates/nope", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == candidate.id
    assert len(body["messages"]) == 2
    assert body["timeline"][0]["type"] == "llm_usage"
    assert missing.status_code == 404


def test_moves_to_interview_and_persists(
    client: TestClient, repository: InMemoryCandidateRepository, crm_clock: FixedClock
) -> None:
    candidate = make_candidate(status=CandidateStatus.QUALIFIED, stage=Stage.DONE)
    repository.save(candidate)

    response = client.post(
        f"/crm/candidates/{candidate.id}/status",
        json={"status": "interview", "recruiter": "Marta"},
        headers=AUTH,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "interview"
    stored = repository.get(candidate.id)
    assert stored is not None
    assert stored.status == CandidateStatus.INTERVIEW
    assert stored.timeline[-1].type == "recruiter_action"
    assert stored.timeline[-1].at == crm_clock.now()


def test_rejects_invalid_status_changes(
    client: TestClient, repository: InMemoryCandidateRepository
) -> None:
    candidate = make_candidate(status=CandidateStatus.QUALIFIED, stage=Stage.DONE)
    repository.save(candidate)
    url = f"/crm/candidates/{candidate.id}/status"

    not_allowed = client.post(url, json={"status": "hired"}, headers=AUTH)
    no_reason = client.post(url, json={"status": "disqualified"}, headers=AUTH)
    not_manual = client.post(url, json={"status": "qualified"}, headers=AUTH)
    missing = client.post("/crm/candidates/nope/status", json={"status": "interview"}, headers=AUTH)

    assert not_allowed.status_code == 409
    assert "qualified" in not_allowed.json()["detail"]
    assert no_reason.status_code == 422
    assert not_manual.status_code == 422
    assert missing.status_code == 404
    stored = repository.get(candidate.id)
    assert stored is not None and stored.status == CandidateStatus.QUALIFIED


def test_metrics(client: TestClient, repository: InMemoryCandidateRepository) -> None:
    repository.save(make_candidate(status=CandidateStatus.QUALIFIED, usage=[usage(cost=0.01)]))
    repository.save(
        make_candidate(status=CandidateStatus.DISQUALIFIED, knockout_reason="no_license")
    )

    response = client.get("/crm/metrics", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["by_status"]["qualified"] == 1
    assert body["disqualification_reasons"] == {"no_license": 1}
    assert body["tokens"]["total_cost_usd"] == pytest.approx(0.01)


def test_router_from_env_only_with_a_token(repository: InMemoryCandidateRepository) -> None:
    clock = FixedClock(T0)

    assert build_crm_router_from_env(repository, clock, env={}) is None
    assert build_crm_router_from_env(repository, clock, env={"CRM_API_TOKEN": "t"}) is not None


def test_create_app_mounts_the_crm_router_only_when_given(
    repository: InMemoryCandidateRepository,
) -> None:
    settings = WhatsAppSettings(verify_token=SecretStr("v"), app_secret=SecretStr("a"))
    clock = FixedClock(T0)

    def build(**kwargs: object) -> TestClient:
        app = create_app(
            settings,
            store=InMemoryInboundMessageStore(),
            handler=LoggingInboundHandler(),
            clock=clock,
            **kwargs,  # type: ignore[arg-type]
        )
        return TestClient(app)

    without = build()
    with_crm = build(crm_router=build_crm_router(repository, clock, TOKEN))

    assert without.get(f"{CRM_PREFIX}/metrics", headers=AUTH).status_code == 404
    assert with_crm.get(f"{CRM_PREFIX}/metrics", headers=AUTH).status_code == 200
