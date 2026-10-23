from datetime import datetime, timedelta

import pytest

from screening.clock import FixedClock


def test_fixed_clock_advances(clock: FixedClock) -> None:
    start = clock.now()

    clock.advance(hours=24)

    assert clock.now() == start + timedelta(hours=24)


def test_fixed_clock_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError):
        FixedClock(datetime(2026, 1, 1))
