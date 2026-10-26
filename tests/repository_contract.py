"""Comportamiento de `CandidateRepository.query`, `count_by_status` y `LeaseStore`.

Se corre contra cada implementación: en memoria (unitario) y Firestore real (integración),
para que ambas digan lo mismo.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from screening.adapters.query import InvalidCursorError
from screening.domain import (
    ApplicationPayload,
    Candidate,
    CandidateStatus,
    Country,
    ReengagementState,
    ScoreBreakdown,
    Stage,
)
from screening.ports import CandidateQuery, CandidateRepository, LeaseStore

T0 = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)


def make(
    candidate_id: str,
    *,
    status: CandidateStatus = CandidateStatus.IN_SCREENING,
    country: Country = Country.ES,
    stage: Stage = Stage.KNOCKOUT,
    minutes: int = 0,
    score: float | None = None,
    next_touch_in_hours: float | None = None,
) -> Candidate:
    return Candidate(
        id=candidate_id,
        application=ApplicationPayload(application_id=candidate_id, source="test"),
        phone=f"+3460000{len(candidate_id):04d}{candidate_id[-2:]}",
        country=country,
        status=status,
        stage=stage,
        score=ScoreBreakdown(total=score) if score is not None else None,
        reengagement=ReengagementState(
            next_touch_at=(
                T0 + timedelta(hours=next_touch_in_hours)
                if next_touch_in_hours is not None
                else None
            )
        ),
        created_at=T0,
        updated_at=T0 + timedelta(minutes=minutes),
    )


SEED = [
    make("c01", minutes=1, score=80, next_touch_in_hours=2),
    make("c02", minutes=2, score=95, country=Country.MX),
    make("c03", minutes=3, status=CandidateStatus.QUALIFIED, stage=Stage.DONE, score=60),
    make("c04", minutes=4, next_touch_in_hours=-1),
    make("c05", minutes=5, status=CandidateStatus.CONTACTED, stage=Stage.WELCOME),
    make("c06", minutes=6, score=95, next_touch_in_hours=30),
]


def ids(page) -> list[str]:
    return [c.id for c in page.items]


def check_query_contract(repo: CandidateRepository) -> None:
    for candidate in SEED:
        repo.save(candidate)

    # orden por actualización, descendente
    assert ids(repo.query(CandidateQuery())) == ["c06", "c05", "c04", "c03", "c02", "c01"]

    # filtros
    q = CandidateQuery(statuses=[CandidateStatus.IN_SCREENING], country=Country.ES)
    assert ids(repo.query(q)) == ["c06", "c04", "c01"]
    assert ids(repo.query(CandidateQuery(stage=Stage.DONE))) == ["c03"]
    assert ids(repo.query(CandidateQuery(statuses=[]))) == []

    # puntaje descendente; sin puntaje al final; empates por id descendente
    by_score = ids(repo.query(CandidateQuery(order="score_desc")))
    assert by_score[:4] == ["c06", "c02", "c01", "c03"]
    assert set(by_score[4:]) == {"c04", "c05"}

    # próximo toque: solo las fichas que lo tienen, ascendente; y los vencidos
    assert ids(repo.query(CandidateQuery(order="next_touch_asc"))) == ["c04", "c01", "c06"]
    due = CandidateQuery(order="next_touch_asc", touch_due_before=T0 + timedelta(hours=3))
    assert ids(repo.query(due)) == ["c04", "c01"]

    # paginación con cursor, sin repetir ni saltar fichas
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        page = repo.query(CandidateQuery(limit=2, cursor=cursor))
        seen += ids(page)
        pages += 1
        cursor = page.next_cursor
        if cursor is None:
            break
    assert seen == ["c06", "c05", "c04", "c03", "c02", "c01"]
    assert pages == 3

    first = repo.query(CandidateQuery(order="score_desc", limit=3))
    second = repo.query(CandidateQuery(order="score_desc", limit=3, cursor=first.next_cursor))
    assert ids(first) + ids(second) == by_score

    with pytest.raises(InvalidCursorError):
        repo.query(
            CandidateQuery(
                order="score_desc", cursor=repo.query(CandidateQuery(limit=1)).next_cursor
            )
        )

    # conteos agregados
    counts = repo.count_by_status()
    assert counts[CandidateStatus.IN_SCREENING] == 4
    assert counts[CandidateStatus.QUALIFIED] == 1
    assert counts[CandidateStatus.CONTACTED] == 1
    assert counts[CandidateStatus.HIRED] == 0


def check_lease_contract(leases: LeaseStore, key: str = "lease-test") -> None:
    ttl = timedelta(seconds=30)
    assert leases.try_acquire(key, "a", now=T0, ttl=ttl)
    assert not leases.try_acquire(key, "b", now=T0 + timedelta(seconds=10), ttl=ttl)
    assert leases.try_acquire(key, "a", now=T0 + timedelta(seconds=10), ttl=ttl)  # renueva
    # vencido: otro dueño lo toma
    assert leases.try_acquire(key, "b", now=T0 + timedelta(seconds=41), ttl=ttl)
    leases.release(key, "a")  # no es el dueño: no hace nada
    assert not leases.try_acquire(key, "c", now=T0 + timedelta(seconds=42), ttl=ttl)
    leases.release(key, "b")
    assert leases.try_acquire(key, "c", now=T0 + timedelta(seconds=43), ttl=ttl)
    leases.release(key, "c")
