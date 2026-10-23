from __future__ import annotations

from datetime import timedelta

import pytest

from screening.crm.metrics import compute_metrics
from screening.domain import CandidateStatus, Country, Stage
from tests.unit.crm.conftest import T0, make_candidate, usage

NOW = T0 + timedelta(days=2)
STALE = timedelta(hours=24)


def qualified_event(minutes: float) -> tuple:
    at = T0 + timedelta(minutes=minutes)
    return (at, "status_changed", {"previous": "in_screening", "new": "qualified"})


def test_empty_list_has_zeros_and_no_rates() -> None:
    metrics = compute_metrics([], NOW, STALE)

    assert metrics.total == 0
    assert set(metrics.by_status) == set(CandidateStatus)
    assert all(v == 0 for v in metrics.by_status.values())
    assert metrics.response_rate is None
    assert metrics.completion_rate is None
    assert metrics.qualification_rate is None
    assert metrics.avg_minutes_to_qualify is None
    assert metrics.avg_score is None
    assert metrics.tokens.total_cost_usd == 0
    assert metrics.tokens.avg_cost_per_candidate_usd is None
    assert metrics.tokens.cost_per_qualified_usd is None


def test_counts_by_status_and_country() -> None:
    candidates = [
        make_candidate(status=CandidateStatus.QUALIFIED),
        make_candidate(status=CandidateStatus.QUALIFIED, country=Country.MX),
        make_candidate(status=CandidateStatus.INVALID_DATA, country=None),
    ]

    metrics = compute_metrics(candidates, NOW, STALE)

    assert metrics.total == 3
    assert metrics.by_status[CandidateStatus.QUALIFIED] == 2
    assert metrics.by_status[CandidateStatus.INVALID_DATA] == 1
    assert metrics.by_status[CandidateStatus.HIRED] == 0
    assert metrics.by_country == {"ES": 1, "MX": 1, "unknown": 1}


def test_funnel_rates() -> None:
    candidates = [
        make_candidate(status=CandidateStatus.NEW),  # nunca contactado
        make_candidate(status=CandidateStatus.CONTACTED, agent_messages=1),  # sin respuesta
        make_candidate(status=CandidateStatus.IN_SCREENING, agent_messages=2, candidate_messages=1),
        make_candidate(
            status=CandidateStatus.DISQUALIFIED,
            agent_messages=2,
            candidate_messages=2,
            knockout_reason="no_license",
        ),
        make_candidate(status=CandidateStatus.QUALIFIED, agent_messages=4, candidate_messages=4),
        make_candidate(status=CandidateStatus.HIRED, agent_messages=4, candidate_messages=4),
    ]

    metrics = compute_metrics(candidates, NOW, STALE)

    assert metrics.contacted == 5
    assert metrics.responded == 4
    assert metrics.completed == 3
    assert metrics.qualified == 2
    assert metrics.response_rate == pytest.approx(4 / 5)
    assert metrics.completion_rate == pytest.approx(3 / 4)
    assert metrics.qualification_rate == pytest.approx(2 / 3)


def test_drop_off_by_stage_counts_closed_and_stale_conversations() -> None:
    fresh = NOW - timedelta(hours=1)
    stale = NOW - timedelta(hours=30)
    candidates = [
        make_candidate(status=CandidateStatus.NO_RESPONSE, stage=Stage.WELCOME),
        make_candidate(status=CandidateStatus.OPTED_OUT, stage=Stage.KNOCKOUT),
        make_candidate(status=CandidateStatus.IN_SCREENING, stage=Stage.PROFILE, updated_at=stale),
        make_candidate(status=CandidateStatus.CONTACTED, stage=Stage.WELCOME, updated_at=stale),
        # Activos hace poco: todavía no cuentan como abandono.
        make_candidate(status=CandidateStatus.IN_SCREENING, stage=Stage.PROFILE, updated_at=fresh),
        make_candidate(status=CandidateStatus.QUALIFIED, stage=Stage.DONE, updated_at=stale),
    ]

    metrics = compute_metrics(candidates, NOW, STALE)

    assert metrics.drop_off_by_stage == {
        Stage.WELCOME: 2,
        Stage.KNOCKOUT: 1,
        Stage.PROFILE: 1,
        Stage.CLOSING: 0,
        Stage.DONE: 0,
    }


def test_disqualification_reasons_use_rule_or_manual() -> None:
    candidates = [
        make_candidate(status=CandidateStatus.DISQUALIFIED, knockout_reason="no_license"),
        make_candidate(status=CandidateStatus.DISQUALIFIED, knockout_reason="no_license"),
        make_candidate(status=CandidateStatus.DISQUALIFIED, knockout_reason="out_of_service_area"),
        make_candidate(status=CandidateStatus.DISQUALIFIED),  # descarte del recruiter
        make_candidate(status=CandidateStatus.QUALIFIED),
    ]

    metrics = compute_metrics(candidates, NOW, STALE)

    assert metrics.disqualification_reasons == {
        "no_license": 2,
        "out_of_service_area": 1,
        "manual": 1,
    }


def test_average_minutes_to_qualify() -> None:
    candidates = [
        make_candidate(status=CandidateStatus.QUALIFIED, events=[qualified_event(10)]),
        make_candidate(status=CandidateStatus.INTERVIEW, events=[qualified_event(20)]),
        make_candidate(status=CandidateStatus.IN_SCREENING),
    ]

    metrics = compute_metrics(candidates, NOW, STALE)

    assert metrics.avg_minutes_to_qualify == pytest.approx(15)


def test_reengagement_touches() -> None:
    candidates = [make_candidate(touches=2), make_candidate(touches=1), make_candidate()]

    metrics = compute_metrics(candidates, NOW, STALE)

    assert metrics.reengagement_touches == 3
    assert metrics.candidates_reengaged == 2


def test_human_requested_and_average_score() -> None:
    candidates = [
        make_candidate(status=CandidateStatus.QUALIFIED, score=80),
        make_candidate(status=CandidateStatus.QUALIFIED, score=60, human_requested=True),
        make_candidate(status=CandidateStatus.IN_SCREENING, human_requested=True),
    ]

    metrics = compute_metrics(candidates, NOW, STALE)

    assert metrics.human_requested == 2
    assert metrics.avg_score == pytest.approx(70)


def test_token_costs_total_and_per_candidate() -> None:
    candidates = [
        make_candidate(
            status=CandidateStatus.QUALIFIED,
            usage=[usage(100, 20, 0.002), usage(200, 40, 0.004)],
        ),
        make_candidate(status=CandidateStatus.IN_SCREENING, usage=[usage(50, 10, 0.003)]),
        make_candidate(status=CandidateStatus.CONTACTED),  # sin llamadas al LLM
    ]

    tokens = compute_metrics(candidates, NOW, STALE).tokens

    assert tokens.calls == 3
    assert tokens.input_tokens == 350
    assert tokens.output_tokens == 70
    assert tokens.total_cost_usd == pytest.approx(0.009)
    assert tokens.avg_cost_per_candidate_usd == pytest.approx(0.0045)
    assert tokens.cost_per_qualified_usd == pytest.approx(0.009)
