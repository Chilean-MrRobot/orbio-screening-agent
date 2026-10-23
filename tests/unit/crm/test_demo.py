from __future__ import annotations

import io
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from screening.adapters.memory import InMemoryCandidateRepository
from screening.channels import ConsoleChannel
from screening.clock import FixedClock
from screening.config import AppConfig
from screening.conversation import ConversationOrchestrator
from screening.crm.demo import DEMO_SCENARIOS, DemoAgent, create_demo_app, seed_in_process
from screening.crm.metrics import compute_metrics
from screening.domain import CandidateStatus, Country


@pytest.fixture
def seeded(
    app_config: AppConfig, clock: FixedClock, repository: InMemoryCandidateRepository
) -> dict[str, str]:
    orchestrator = ConversationOrchestrator(
        repository=repository,
        agent=DemoAgent(app_config, clock),
        channel=ConsoleChannel(io.StringIO()),
        clock=clock,
        config=app_config,
    )
    return seed_in_process(
        orchestrator, repository, clock, advance=lambda: clock.advance(minutes=2)
    )


def test_there_are_twelve_scenarios_in_both_countries() -> None:
    assert len(DEMO_SCENARIOS) == 12
    assert {s.application["country"] for s in DEMO_SCENARIOS} == {"ES", "MX"}
    assert len({s.application["phone"] for s in DEMO_SCENARIOS}) == 12


@pytest.mark.parametrize("scenario", DEMO_SCENARIOS, ids=lambda s: s.key)
def test_each_scenario_ends_in_the_expected_status(
    scenario, seeded: dict[str, str], repository: InMemoryCandidateRepository
) -> None:
    candidate = repository.get(seeded[scenario.key])

    assert candidate is not None
    assert candidate.status == scenario.expected_status


def test_the_demo_covers_every_knockout_rule(
    seeded: dict[str, str], repository: InMemoryCandidateRepository, app_config: AppConfig
) -> None:
    reasons = {
        c.knockout.reason_code
        for c in repository.list(CandidateStatus.DISQUALIFIED)
        if c.knockout is not None
    }

    assert reasons == {r.reason_code for r in app_config.knockout_rules}


def test_the_demo_feeds_every_panel(
    seeded: dict[str, str], repository: InMemoryCandidateRepository, clock: FixedClock
) -> None:
    candidates = repository.list()
    metrics = compute_metrics(candidates, clock.now(), timedelta(hours=24))

    assert metrics.total == 12
    assert metrics.human_requested == 1
    assert metrics.qualified == 4
    assert metrics.avg_minutes_to_qualify is not None
    assert metrics.tokens.calls > 0
    assert metrics.tokens.total_cost_usd > 0
    qualified = [c for c in candidates if c.score is not None]
    assert len({c.score.total for c in qualified if c.score}) > 1  # puntajes distintos
    assert any(c.country == Country.MX for c in qualified)


def test_demo_app_serves_the_crm_with_the_default_token() -> None:
    app = create_demo_app(env={"SCREENING_REPOSITORY": "memory"})
    client = TestClient(app)

    response = client.get("/crm/metrics", headers={"Authorization": "Bearer demo-token"})

    assert response.status_code == 200
    assert response.json()["total"] == 0
