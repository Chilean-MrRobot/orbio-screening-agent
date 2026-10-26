"""La pasada lee por índice, pagina, respeta el tope y se puede reanudar (spec 017)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from screening.adapters.memory import InMemoryCandidateRepository
from screening.domain import Candidate, CandidateStatus
from screening.ports import CandidatePage, CandidateQuery
from screening.reengagement import TouchOutcome, backfill_next_touch, run_due_touches
from tests.unit.reengagement.conftest import MONDAY, Harness, abandoned, contacted

H = timedelta(hours=1)


class SpyRepository(InMemoryCandidateRepository):
    """Registra las consultas; `list()` está prohibido. Puede fallar en la consulta N."""

    def __init__(self) -> None:
        super().__init__()
        self.queries: list[CandidateQuery] = []
        self.fail_on_query: int | None = None

    def query(self, query: CandidateQuery) -> CandidatePage:
        self.queries.append(query)
        if self.fail_on_query == len(self.queries):
            self.fail_on_query = None
            raise ConnectionError("firestore unavailable")
        return super().query(query)

    def list(self, status: CandidateStatus | None = None) -> list[Candidate]:
        raise AssertionError("la pasada no debe recorrer todas las fichas")

    def all(self) -> list[Candidate]:
        return super().list()


@pytest.fixture
def spy() -> SpyRepository:
    return SpyRepository()


@pytest.fixture
def indexed(harness: Harness, spy: SpyRepository) -> Harness:
    harness.repository = spy
    return harness


def _phone(i: int) -> str:
    return f"+3460011{i:04d}"


def _add_contacted(harness: Harness, count: int, *, start: int = 0) -> list[str]:
    """Fichas contactadas una hora después de la otra: la primera es la más atrasada."""
    return [
        harness.add(contacted(MONDAY + i * H, phone=_phone(i))) for i in range(start, start + count)
    ]


def _reminders(spy: SpyRepository) -> dict[str, int]:
    return {
        c.id: sum(1 for e in c.timeline if e.type == "reengagement_touch_sent") for c in spy.all()
    }


def test_the_pass_reads_only_due_candidates(indexed: Harness, spy: SpyRepository) -> None:
    due = _add_contacted(indexed, 3)
    later = indexed.add(contacted(MONDAY + 10 * H, phone=_phone(9)))
    final = contacted(MONDAY, phone=_phone(8))
    final.status = CandidateStatus.QUALIFIED
    indexed.add(final)

    report = indexed.run_at(MONDAY + 26 * H)

    assert report.read == 3
    assert {t.candidate_id for t in report.touches} == set(due)
    assert indexed.get(later).reengagement.cadence is None
    [query] = spy.queries
    assert query.touch_due_before == MONDAY + 26 * H
    assert query.order == "next_touch_asc"


def test_the_pass_follows_the_cursor_across_pages(indexed: Harness, spy: SpyRepository) -> None:
    ids = _add_contacted(indexed, 5)

    report = indexed.run_at(MONDAY + 30 * H, page_size=2)

    assert report.read == 5
    assert not report.capped
    assert {t.candidate_id for t in report.touches if t.outcome == TouchOutcome.SENT} == set(ids)
    assert [q.limit for q in spy.queries] == [2, 2, 2]
    assert spy.queries[0].cursor is None
    assert all(q.cursor for q in spy.queries[1:])


def test_the_cap_stops_the_pass_and_the_next_one_continues(
    indexed: Harness, spy: SpyRepository
) -> None:
    ids = _add_contacted(indexed, 5)

    first = indexed.run_at(MONDAY + 30 * H, page_size=2, max_per_pass=3)

    assert first.read == 3
    assert first.capped
    assert [t.candidate_id for t in first.touches] == ids[:3]  # las más atrasadas primero
    assert [q.limit for q in spy.queries] == [2, 1]

    second = indexed.run_at(MONDAY + 30 * H + timedelta(minutes=15), page_size=2, max_per_pass=3)

    assert second.read == 2
    assert not second.capped
    assert [t.candidate_id for t in second.touches] == ids[3:]
    assert _reminders(spy) == dict.fromkeys(ids, 1)


def test_cap_equal_to_the_due_candidates_is_not_capped(indexed: Harness) -> None:
    _add_contacted(indexed, 3)

    report = indexed.run_at(MONDAY + 30 * H, page_size=3, max_per_pass=3)

    assert report.read == 3
    assert not report.capped


def test_repeating_the_pass_sends_nothing_twice(indexed: Harness, spy: SpyRepository) -> None:
    ids = _add_contacted(indexed, 4)

    indexed.run_at(MONDAY + 30 * H, page_size=2)
    again = indexed.run_at(MONDAY + 30 * H, page_size=2)

    assert again.read == 0
    assert again.touches == []
    assert _reminders(spy) == dict.fromkeys(ids, 1)


def test_an_interrupted_pass_resumes_without_duplicates(
    indexed: Harness, spy: SpyRepository
) -> None:
    ids = _add_contacted(indexed, 5)
    spy.fail_on_query = 2  # la base se cae al pedir la segunda página

    with pytest.raises(ConnectionError):
        indexed.run_at(MONDAY + 30 * H, page_size=2)
    assert sum(_reminders(spy).values()) == 2

    report = indexed.run_at(MONDAY + 30 * H + timedelta(minutes=15), page_size=2)

    assert report.read == 3
    assert _reminders(spy) == dict.fromkeys(ids, 1)
    assert len(indexed.whatsapp.sent) == 5


def test_a_failed_touch_stays_due_and_is_retried(indexed: Harness, spy: SpyRepository) -> None:
    [cid] = _add_contacted(indexed, 1)
    indexed.whatsapp.fail = True

    first = indexed.run_at(MONDAY + 24 * H)
    assert [t.outcome for t in first.touches] == [TouchOutcome.FAILED]
    assert indexed.get(cid).reengagement.next_touch_at == MONDAY + 24 * H

    indexed.whatsapp.fail = False
    second = indexed.run_at(MONDAY + 24 * H + timedelta(minutes=15))
    assert [t.outcome for t in second.touches] == [TouchOutcome.SENT]
    assert indexed.get(cid).reengagement.next_touch_at == MONDAY + 72 * H


def test_due_candidate_without_cadence_leaves_the_index(
    indexed: Harness, spy: SpyRepository
) -> None:
    stale = contacted(MONDAY)
    stale.status = CandidateStatus.QUALIFIED
    stale.reengagement.next_touch_at = MONDAY + 24 * H  # dato viejo
    spy.save(stale)

    report = indexed.run_at(MONDAY + 25 * H)

    assert report.read == 1
    assert report.evaluated == 0
    assert indexed.get(stale.id).reengagement.next_touch_at is None
    assert indexed.run_at(MONDAY + 26 * H).read == 0


def test_the_closing_step_leaves_the_index(indexed: Harness) -> None:
    [cid] = _add_contacted(indexed, 1)
    for hours in (24, 72, 120):
        indexed.run_at(MONDAY + hours * H)

    assert indexed.get(cid).reengagement.next_touch_at is None
    assert indexed.run_at(MONDAY + 200 * H).read == 0


def test_spy_repository_rejects_list(spy: SpyRepository) -> None:
    with pytest.raises(AssertionError):
        spy.list()


def test_run_due_touches_with_an_empty_index(indexed: Harness, spy: SpyRepository) -> None:
    report = run_due_touches(
        repository=spy, channels=indexed.channels, clock=indexed.clock, config=indexed.config
    )

    assert report.read == 0
    assert not report.capped


# --- Backfill -----------------------------------------------------------------------


def test_backfill_schedules_old_candidates(indexed: Harness, spy: SpyRepository) -> None:
    # Fichas de antes de la spec 017: guardadas sin `next_touch_at`.
    old_contacted = contacted(MONDAY, phone=_phone(1))
    old_abandoned = abandoned(MONDAY, phone=_phone(2))
    closed = contacted(MONDAY, phone=_phone(3))
    closed.status = CandidateStatus.QUALIFIED
    for candidate in (old_contacted, old_abandoned, closed):
        spy.save(candidate)
    assert indexed.run_at(MONDAY + 24 * H).read == 0  # la consulta no las ve

    report = backfill_next_touch(spy, indexed.config, page_size=1)

    assert report.read == 2  # solo las abiertas
    assert report.updated == 2
    assert indexed.get(old_contacted.id).reengagement.next_touch_at == MONDAY + 24 * H
    assert indexed.get(old_abandoned.id).reengagement.next_touch_at == MONDAY + 24 * H
    assert indexed.get(closed.id).reengagement.next_touch_at is None

    sent = indexed.run_at(MONDAY + 24 * H + timedelta(minutes=15))
    assert {t.candidate_id for t in sent.touches} == {old_contacted.id, old_abandoned.id}


def test_backfill_is_idempotent(indexed: Harness, spy: SpyRepository) -> None:
    spy.save(contacted(MONDAY, phone=_phone(1)))
    _add_contacted(indexed, 2, start=2)  # ya agendadas

    first = backfill_next_touch(spy, indexed.config)
    second = backfill_next_touch(spy, indexed.config)

    assert (first.read, first.updated) == (3, 1)
    assert (second.read, second.updated) == (3, 0)


def test_backfill_keeps_an_active_cadence(indexed: Harness, spy: SpyRepository) -> None:
    [cid] = _add_contacted(indexed, 1)
    indexed.run_at(MONDAY + 24 * H)
    candidate = indexed.get(cid)
    candidate.reengagement.next_touch_at = None  # p. ej. borrado por un deploy anterior
    spy.save(candidate)

    report = backfill_next_touch(spy, indexed.config)

    assert report.updated == 1
    assert indexed.get(cid).reengagement.next_touch_at == MONDAY + 72 * H
