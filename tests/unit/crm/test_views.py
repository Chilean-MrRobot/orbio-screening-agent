from __future__ import annotations

from datetime import timedelta

import pytest

from screening.crm import views
from screening.crm.listing import to_row
from screening.crm.metrics import compute_metrics
from screening.domain import (
    CandidateStatus,
    DeliveryExperience,
    ScoreBreakdown,
    Stage,
    TimelineEvent,
)
from tests.unit.crm.conftest import T0, make_candidate


def test_every_status_and_stage_has_a_label() -> None:
    assert set(views.STATUS_LABELS) == set(CandidateStatus)
    assert set(views.STATUS_ICONS) == set(CandidateStatus)
    assert set(views.STAGE_LABELS) == set(Stage)


def test_table_records() -> None:
    candidate = make_candidate(
        status=CandidateStatus.DISQUALIFIED,
        knockout_reason="no_license",
        agent_messages=1,
        candidate_messages=1,
        human_requested=True,
    )
    candidate.messages[-1].text = "x" * 100

    (record,) = views.table_records([to_row(candidate)])

    assert record["Estado"] == "🔴 Descartado"
    assert record["Etapa"] == "Bienvenida"
    assert record["Humano"] == "🙋 Sí"
    assert record["Motivo"] == "Sin licencia vigente"
    assert record["Último mensaje"] == "👤 " + "x" * 80 + "…"
    assert record["País"] == "ES"


def test_chat_messages_map_roles_to_avatars() -> None:
    candidate = make_candidate(agent_messages=1, candidate_messages=1)

    messages = views.chat_messages(candidate)

    assert [m["avatar"] for m in messages] == ["assistant", "user"]
    assert messages[0]["text"] == "agente 0"
    assert "console" in messages[0]["caption"]


def test_data_rows_mark_missing_and_review_fields() -> None:
    candidate = make_candidate()
    candidate.data.has_license = True
    candidate.data.delivery_experience = DeliveryExperience(years=2, platforms=["Glovo", "Uber"])
    candidate.data.needs_review = ["start_date"]

    rows = {r["Campo"]: r["Valor"] for r in views.data_rows(candidate)}

    assert rows["Tiene licencia"] == "Sí"
    assert rows["Experiencia en reparto"] == "2 años (Glovo, Uber)"
    assert rows["Fecha de inicio"] == "⚠️ a revisar"
    assert rows["Ciudad"] == "—"


def test_score_rows_sorted_by_points() -> None:
    candidate = make_candidate()
    assert views.score_rows(candidate) == []

    candidate.score = ScoreBreakdown(
        total=55, components={"availability": 25, "delivery_experience": 30}
    )

    assert views.score_rows(candidate) == [
        {"Componente": "Delivery experience", "Puntos": 30},
        {"Componente": "Availability", "Puntos": 25},
    ]


def test_timeline_rows_newest_first_with_readable_details() -> None:
    timeline = [
        TimelineEvent(
            at=T0,
            type="status_changed",
            detail={"previous": "in_screening", "new": "disqualified", "reason": "no_license"},
        ),
        TimelineEvent(
            at=T0 + timedelta(minutes=1),
            type="field_updated",
            detail={"field": "owns_vehicle", "value": True, "previous": None},
        ),
        TimelineEvent(
            at=T0 + timedelta(minutes=2),
            type="knockouts_evaluated",
            detail={"passed": ["consent_given"], "failed_rule": None},
        ),
        TimelineEvent(at=T0 + timedelta(minutes=3), type="custom", detail={"a": 1, "b": None}),
    ]

    rows = views.timeline_rows(timeline)

    assert [r["Evento"] for r in rows] == [
        "custom",
        "Reglas evaluadas",
        "Dato extraído",
        "Cambio de estado",
    ]
    assert rows[0]["Detalle"] == "a=1"
    assert rows[1]["Detalle"] == "Pasaron: consent_given"
    assert rows[2]["Detalle"] == "Vehículo propio: Sí"
    assert rows[3]["Detalle"] == "En screening → Descartado (Sin licencia vigente)"


def test_describe_recruiter_action_and_usage() -> None:
    action = TimelineEvent(
        at=T0,
        type="recruiter_action",
        detail={"status": "disqualified", "reason": "No se presentó", "recruiter": "Marta"},
    )
    usage = TimelineEvent(
        at=T0,
        type="llm_usage",
        detail={
            "turn": "reply",
            "model": "gpt-5-mini",
            "input_tokens": 120,
            "output_tokens": 30,
            "cost_usd": 0.00009,
        },
    )

    assert views.describe_event(action) == "Marta: Descartado · No se presentó"
    assert views.describe_event(usage) == "reply · gpt-5-mini · 120+30 tokens · US$ 0.00009"


@pytest.mark.parametrize(
    ("minutes", "text"),
    [(None, "—"), (0.5, "30 s"), (12.4, "12 min"), (90, "1.5 h"), (60 * 36, "1.5 días")],
)
def test_format_minutes(minutes: float | None, text: str) -> None:
    assert views.format_minutes(minutes) == text


def test_metric_series_use_readable_labels() -> None:
    candidates = [
        make_candidate(status=CandidateStatus.NO_RESPONSE, stage=Stage.KNOCKOUT),
        make_candidate(status=CandidateStatus.DISQUALIFIED, knockout_reason="no_license"),
        make_candidate(status=CandidateStatus.DISQUALIFIED),
    ]
    metrics = compute_metrics(candidates, T0, timedelta(hours=24))

    assert views.funnel(metrics)["Postulaciones"] == 3
    assert views.status_counts(metrics) == {"Descartado": 2, "Sin respuesta": 1}
    assert views.drop_off_counts(metrics) == {
        "Bienvenida": 0,
        "Requisitos": 1,
        "Perfil": 0,
        "Cierre": 0,
    }
    assert views.reason_counts(metrics) == {
        "Sin licencia vigente": 1,
        "Descarte del recruiter": 1,
    }


def test_format_rate() -> None:
    assert views.format_rate(None) == "—"
    assert views.format_rate(0.756) == "76%"
