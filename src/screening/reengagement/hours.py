"""Horario hábil por país, en hora local (`business_hours` de `cadences.yaml`)."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from screening.config.models import BusinessHours


def next_opening(at: datetime, hours: BusinessHours) -> datetime:
    """Primer instante desde `at` dentro del horario hábil, en UTC (`at` si ya está abierto)."""
    tz = ZoneInfo(hours.timezone)
    local_day = at.astimezone(tz).date()
    for offset in range(8):
        day = local_day + timedelta(days=offset)
        if day.isoweekday() not in hours.days:
            continue
        midnight = datetime.combine(day, time(0), tzinfo=tz)
        opens = (midnight + timedelta(hours=hours.start_hour)).astimezone(UTC)
        closes = (midnight + timedelta(hours=hours.end_hour)).astimezone(UTC)
        if at < closes:
            return max(at.astimezone(UTC), opens)
    raise ValueError(f"horario hábil sin días válidos: {hours.days}")


def is_open(at: datetime, hours: BusinessHours) -> bool:
    return next_opening(at, hours) == at
