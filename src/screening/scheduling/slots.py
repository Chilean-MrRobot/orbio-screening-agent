"""Horarios de entrevista: generación desde la configuración, reparto y formato (spec 021).

Funciones puras, sin reloj ni base de datos: el instante de referencia llega como argumento.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from screening.config.models import CountryScheduling
from screening.domain import InterviewSlot, Language

WEEKDAYS = {
    Language.ES: ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"),
    Language.EN: ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
}
MONTHS = {
    Language.ES: (
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre",
    ),
    Language.EN: (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
}


def slot_id(scope: str, start: datetime) -> str:
    """`ES_20261027T0900Z`: ordena como la hora de inicio dentro del `scope`."""
    return f"{scope}_{start.astimezone(UTC):%Y%m%dT%H%M}Z"


def generate_slots(
    spec: CountryScheduling,
    timezone: str,
    *,
    slot_minutes: int,
    start: datetime,
    end: datetime,
) -> list[InterviewSlot]:
    """Horarios de las ventanas del país con inicio en [start, end), en orden y en UTC."""
    zone = ZoneInfo(timezone)
    duration = timedelta(minutes=slot_minutes)
    day = start.astimezone(zone).date()
    last = end.astimezone(zone).date()
    slots: list[InterviewSlot] = []
    while day <= last:
        if day.isoweekday() in spec.days:
            for window in spec.windows:
                slots.extend(_window_slots(day, window.start, window.end, zone, duration))
        day += timedelta(days=1)
    return [s for s in sorted(slots, key=lambda s: s.start) if start <= s.start < end]


def pick_options(free: list[InterviewSlot], count: int, timezone: str) -> list[InterviewSlot]:
    """El primer horario libre de cada día distinto; si faltan días, los siguientes libres."""
    zone = ZoneInfo(timezone)
    chosen: list[InterviewSlot] = []
    days: set[date] = set()
    for slot in free:
        local_day = slot.start.astimezone(zone).date()
        if local_day not in days:
            chosen.append(slot)
            days.add(local_day)
        if len(chosen) == count:
            break
    for slot in free:
        if len(chosen) == count:
            break
        if slot not in chosen:
            chosen.append(slot)
    return sorted(chosen, key=lambda s: s.start)


def format_slot(slot: InterviewSlot, timezone: str, language: Language) -> str:
    """`martes 27 de octubre, 10:00` / `Tuesday, October 27, 10:00`, en hora local."""
    local = slot.start.astimezone(ZoneInfo(timezone))
    weekday = WEEKDAYS[language][local.weekday()]
    month = MONTHS[language][local.month - 1]
    if language == Language.EN:
        return f"{weekday}, {month} {local.day}, {local:%H:%M}"
    return f"{weekday} {local.day} de {month}, {local:%H:%M}"


def format_options(slots: Iterable[InterviewSlot], timezone: str, language: Language) -> list[str]:
    return [f"{i}) {format_slot(s, timezone, language)}" for i, s in enumerate(slots, start=1)]


def _window_slots(
    day: date, start: time, end: time, zone: ZoneInfo, duration: timedelta
) -> list[InterviewSlot]:
    """Aritmética en hora local (de pared): con cambio de horario, 10:00 sigue siendo 10:00."""
    current = datetime.combine(day, start, tzinfo=zone)
    close = datetime.combine(day, end, tzinfo=zone)
    slots = []
    while current + duration <= close:
        utc = current.astimezone(UTC)
        slots.append(InterviewSlot(start=utc, end=utc + duration))
        current += duration
    return slots
