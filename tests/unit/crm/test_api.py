from __future__ import annotations

from datetime import timedelta
from typing import Any

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

PASSWORD = "s3cret"
AUTH = {"Authorization": f"Bearer {PASSWORD}"}


class NoListRepository(InMemoryCandidateRepository):
    """Falla si alguien recorre todas las fichas: el panel debe usar `query`."""

    def list(self, status: CandidateStatus | None = None) -> list[Any]:
        raise AssertionError("el panel no debe usar repository.list()")


@pytest.fixture
def repository() -> InMemoryCandidateRepository:
    return NoListRepository()


@pytest.fixture
def crm_clock() -> FixedClock:
    return FixedClock(T0 + timedelta(hours=1))


def make_client(
    repository: InMemoryCandidateRepository, clock: FixedClock, **kwargs: Any
) -> TestClient:
    app = FastAPI()
    app.include_router(build_crm_router(repository, clock, PASSWORD, **kwargs))
    return TestClient(app)


@pytest.fixture
def client(repository: InMemoryCandidateRepository, crm_clock: FixedClock) -> TestClient:
    return make_client(repository, crm_clock)


def all_pages(client: TestClient, params: list[tuple[str, str]]) -> list[list[str]]:
    pages: list[list[str]] = []
    cursor: str | None = None
    while True:
        query = params + ([("cursor", cursor)] if cursor else [])
        response = client.get("/crm/candidates", params=query, headers=AUTH)
        assert response.status_code == 200
        body = response.json()
        pages.append([r["id"] for r in body["items"]])
        cursor = body["next_cursor"]
        if cursor is None:
            return pages


# --- Contraseña única -------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/crm/candidates"),
        ("get", "/crm/candidates/x"),
        ("post", "/crm/candidates/x/status"),
        ("get", "/crm/counts"),
        ("get", "/crm/metrics"),
    ],
)
@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": PASSWORD}]
)
def test_every_route_requires_the_password(
    client: TestClient, method: str, path: str, headers: dict[str, str]
) -> None:
    response = client.request(method, path, headers=headers, json={"status": "interview"})

    assert response.status_code == 401


def test_the_password_opens_the_api(client: TestClient) -> None:
    assert client.get("/crm/counts", headers=AUTH).status_code == 200


def test_router_from_env_only_with_the_panel_password(
    repository: InMemoryCandidateRepository,
) -> None:
    clock = FixedClock(T0)

    assert build_crm_router_from_env(repository, clock, env={}) is None
    assert build_crm_router_from_env(repository, clock, env={"CRM_API_TOKEN": "t"}) is None
    router = build_crm_router_from_env(repository, clock, env={"PANEL_PASSWORD": "p"})
    assert router is not None

    app = FastAPI()
    app.include_router(router)
    api = TestClient(app)
    assert api.get("/crm/counts", headers={"Authorization": "Bearer p"}).status_code == 200


def test_metrics_window_from_env(repository: InMemoryCandidateRepository) -> None:
    for _ in range(3):
        repository.save(make_candidate())
    router = build_crm_router_from_env(
        repository, FixedClock(T0), env={"PANEL_PASSWORD": "p", "PANEL_METRICS_WINDOW": "2"}
    )
    assert router is not None
    app = FastAPI()
    app.include_router(router)

    body = TestClient(app).get("/crm/metrics", headers={"Authorization": "Bearer p"}).json()

    assert body["sample_size"] == 2
    assert body["sample_limit"] == 2


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
    with_crm = build(crm_router=build_crm_router(repository, clock, PASSWORD))

    assert without.get(f"{CRM_PREFIX}/metrics", headers=AUTH).status_code == 404
    assert with_crm.get(f"{CRM_PREFIX}/metrics", headers=AUTH).status_code == 200


# --- Listado paginado -------------------------------------------------------------


def test_lists_sorted_by_score(client: TestClient, repository: InMemoryCandidateRepository) -> None:
    low = make_candidate(status=CandidateStatus.QUALIFIED, score=50)
    high = make_candidate(status=CandidateStatus.QUALIFIED, score=95)
    pending = make_candidate(status=CandidateStatus.IN_SCREENING)
    for c in (low, pending, high):
        repository.save(c)

    response = client.get("/crm/candidates", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert [r["id"] for r in body["items"]] == [high.id, low.id, pending.id]
    assert body["items"][0]["score"] == 95
    assert body["items"][0]["status"] == "qualified"
    assert body["next_cursor"] is None


def test_walks_several_pages_without_repeating(
    client: TestClient, repository: InMemoryCandidateRepository
) -> None:
    candidates = [make_candidate(status=CandidateStatus.QUALIFIED, score=10 * i) for i in range(7)]
    for c in candidates:
        repository.save(c)

    pages = all_pages(client, [("limit", "3")])

    assert [len(p) for p in pages] == [3, 3, 1]
    expected = [c.id for c in sorted(candidates, key=lambda c: -c.score.total)]  # type: ignore[union-attr]
    assert [i for page in pages for i in page] == expected


def test_paginates_with_filters(
    client: TestClient, repository: InMemoryCandidateRepository
) -> None:
    wanted: set[str] = set()
    for i in range(5):
        mx = make_candidate(status=CandidateStatus.QUALIFIED, country=Country.MX, score=i)
        es = make_candidate(status=CandidateStatus.QUALIFIED, country=Country.ES, score=i)
        other = make_candidate(status=CandidateStatus.INTERVIEW, country=Country.MX, score=i)
        for c in (mx, es, other):
            repository.save(c)
        wanted.add(mx.id)

    pages = all_pages(client, [("status", "qualified"), ("country", "MX"), ("limit", "2")])

    assert [len(p) for p in pages] == [2, 2, 1]
    assert {i for page in pages for i in page} == wanted


def test_filters_by_several_statuses_and_stage(
    client: TestClient, repository: InMemoryCandidateRepository
) -> None:
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

    assert {r["id"] for r in by_status.json()["items"]} == {mx.id, mx_interview.id}
    assert [r["id"] for r in by_stage.json()["items"]] == [screening.id]
    assert invalid.status_code == 422


def test_orders_by_recent_activity(
    client: TestClient, repository: InMemoryCandidateRepository
) -> None:
    old = make_candidate(score=90, updated_at=T0)
    recent = make_candidate(score=10, updated_at=T0 + timedelta(minutes=5))
    for c in (old, recent):
        repository.save(c)

    response = client.get("/crm/candidates", params={"order": "updated_desc"}, headers=AUTH)

    assert [r["id"] for r in response.json()["items"]] == [recent.id, old.id]


@pytest.mark.parametrize("params", [{"limit": "0"}, {"limit": "201"}, {"order": "next_touch_asc"}])
def test_rejects_invalid_page_parameters(client: TestClient, params: dict[str, str]) -> None:
    assert client.get("/crm/candidates", params=params, headers=AUTH).status_code == 422


def test_invalid_cursor_is_a_bad_request(
    client: TestClient, repository: InMemoryCandidateRepository
) -> None:
    for i in range(3):
        repository.save(make_candidate(score=i))
    by_date = client.get(
        "/crm/candidates", params={"order": "updated_desc", "limit": "1"}, headers=AUTH
    ).json()["next_cursor"]

    garbage = client.get("/crm/candidates", params={"cursor": "not-a-cursor"}, headers=AUTH)
    other_order = client.get(
        "/crm/candidates", params={"order": "score_desc", "cursor": by_date}, headers=AUTH
    )

    assert garbage.status_code == 400
    assert other_order.status_code == 400
    assert "cursor" in other_order.json()["detail"]


# --- Ficha y transiciones ---------------------------------------------------------


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


# --- Conteos y métricas -----------------------------------------------------------


def test_counts_by_status(client: TestClient, repository: InMemoryCandidateRepository) -> None:
    for status in (CandidateStatus.QUALIFIED, CandidateStatus.QUALIFIED, CandidateStatus.NEW):
        repository.save(make_candidate(status=status))

    body = client.get("/crm/counts", headers=AUTH).json()

    assert body["total"] == 3
    assert body["by_status"]["qualified"] == 2
    assert body["by_status"]["new"] == 1
    assert body["by_status"]["hired"] == 0


def test_metrics(client: TestClient, repository: InMemoryCandidateRepository) -> None:
    repository.save(make_candidate(status=CandidateStatus.QUALIFIED, usage=[usage(cost=0.01)]))
    repository.save(
        make_candidate(status=CandidateStatus.DISQUALIFIED, knockout_reason="no_license")
    )

    response = client.get("/crm/metrics", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["sample_size"] == 2
    assert body["by_status"]["qualified"] == 1
    assert body["disqualification_reasons"] == {"no_license": 1}
    assert body["tokens"]["total_cost_usd"] == pytest.approx(0.01)


def test_metrics_use_counts_for_totals_and_a_bounded_window_for_the_rest(
    repository: InMemoryCandidateRepository, crm_clock: FixedClock
) -> None:
    old = [
        make_candidate(
            status=CandidateStatus.DISQUALIFIED,
            knockout_reason="no_license",
            updated_at=T0 + timedelta(seconds=i),
        )
        for i in range(4)
    ]
    recent = [
        make_candidate(status=CandidateStatus.QUALIFIED, updated_at=T0 + timedelta(minutes=10 + i))
        for i in range(3)
    ]
    for c in old + recent:
        repository.save(c)
    client = make_client(repository, crm_clock, metrics_window=3)

    body = client.get("/crm/metrics", headers=AUTH).json()

    assert body["total"] == 7
    assert body["by_status"]["disqualified"] == 4
    assert body["by_status"]["qualified"] == 3
    assert body["sample_size"] == 3
    assert body["sample_limit"] == 3
    assert body["qualified"] == 3  # solo la ventana: las 3 más recientes
    assert body["disqualification_reasons"] == {}


def test_metrics_window_reads_several_query_pages(
    repository: InMemoryCandidateRepository, crm_clock: FixedClock
) -> None:
    for i in range(1205):
        repository.save(make_candidate(updated_at=T0 + timedelta(seconds=i)))
    client = make_client(repository, crm_clock, metrics_window=1100)

    body = client.get("/crm/metrics", headers=AUTH).json()

    assert body["total"] == 1205
    assert body["sample_size"] == 1100
