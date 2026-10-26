from __future__ import annotations

from screening.crm.pagination import Pager


def test_starts_on_the_first_page() -> None:
    pager = Pager()

    assert pager.cursor is None
    assert pager.page_number == 1
    assert not pager.has_previous


def test_next_and_previous_follow_the_cursors() -> None:
    pager = Pager()

    pager.next("c2")
    pager.next("c3")

    assert pager.cursor == "c3"
    assert pager.page_number == 3
    assert pager.has_previous

    pager.previous()

    assert pager.cursor == "c2"
    assert pager.page_number == 2


def test_does_not_advance_without_a_next_cursor() -> None:
    pager = Pager()
    pager.next("c2")

    pager.next(None)

    assert pager.page_number == 2


def test_previous_on_the_first_page_stays_there() -> None:
    pager = Pager()

    pager.previous()

    assert pager.page_number == 1
    assert pager.cursor is None


def test_changing_filters_goes_back_to_the_first_page() -> None:
    pager = Pager()
    pager.sync(("qualified", "ES"))
    pager.next("c2")
    pager.next("c3")

    pager.sync(("qualified", "ES"))
    assert pager.page_number == 3  # mismos filtros: el refresco no mueve la página

    pager.sync(("qualified", "MX"))
    assert pager.page_number == 1
    assert pager.cursor is None


def test_reset() -> None:
    pager = Pager()
    pager.next("c2")

    pager.reset()

    assert pager.page_number == 1
