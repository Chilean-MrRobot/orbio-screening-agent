"""Horarios libres, reserva y cancelación de entrevistas (spec 021).

Lógica determinista: los horarios salen de `scheduling.yaml` y de las reservas del
`InterviewSlotStore`, que es lo único compartido entre instancias.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from screening.clock import Clock
from screening.config import AppConfig
from screening.config.models import CountryScheduling, SchedulingConfig
from screening.domain import (
    Candidate,
    CandidateStatus,
    Country,
    Interview,
    InterviewModality,
    InterviewSlot,
    Stage,
)
from screening.ports import InterviewSlotStore
from screening.rules.specials import normalize_text
from screening.scheduling.slots import format_options, format_slot, generate_slots, pick_options
from screening.scheduling.slots import slot_id as make_slot_id

# Pseudo-campo de la extracción en la etapa `scheduling` (como `summary_confirmed` en el cierre).
SLOT_FIELD = "interview_slot"
AGENT_BOOKER = "agent"


def awaiting_choice(candidate: Candidate) -> bool:
    """Calificado y con una oferta de horarios pendiente de respuesta."""
    return candidate.status == CandidateStatus.QUALIFIED and candidate.stage == Stage.SCHEDULING


def mark_manual(candidate: Candidate, reason: str, now: datetime) -> None:
    """Termina la conversación automática: el recruiter agenda a mano. Sigue `qualified`."""
    state = candidate.scheduling
    state.offered = []
    state.manual_required = True
    state.manual_reason = reason
    candidate.set_stage(Stage.DONE, now)
    candidate.record_event(now, "interview_manual_required", reason=reason)


class InterviewScheduler:
    def __init__(self, config: AppConfig, store: InterviewSlotStore, clock: Clock) -> None:
        self._config = config
        self._store = store
        self._clock = clock

    @property
    def settings(self) -> SchedulingConfig | None:
        scheduling = self._config.scheduling
        return scheduling if scheduling is not None and scheduling.enabled else None

    def enabled_for(self, candidate: Candidate) -> bool:
        settings = self.settings
        return (
            settings is not None
            and candidate.country is not None
            and candidate.country in settings.countries
            and candidate.country in self._config.regions
        )

    def timezone(self, country: Country) -> str:
        return self._config.regions[country].timezone

    # --- Horarios -----------------------------------------------------------------

    def free_slots(
        self,
        country: Country,
        *,
        exclude: Iterable[InterviewSlot] = (),
        limit: int | None = None,
    ) -> list[InterviewSlot]:
        """Horarios válidos desde ahora + antelación, sin los reservados ni los excluidos."""
        slots, start, end = self._valid_slots(country)
        if not slots:
            return []
        taken = self._store.reserved_between(country.value, start, end)
        excluded = {s.start for s in exclude}
        free = [
            s
            for s in slots
            if make_slot_id(country.value, s.start) not in taken and s.start not in excluded
        ]
        return free[:limit] if limit is not None else free

    def options(self, candidate: Candidate) -> list[InterviewSlot]:
        """Las opciones a ofrecer, repartidas en días distintos, sin las ya rechazadas."""
        settings = self.settings
        if settings is None or not self.enabled_for(candidate):
            return []
        assert candidate.country is not None
        free = self.free_slots(candidate.country, exclude=candidate.scheduling.declined)
        timezone = self.timezone(candidate.country)
        return pick_options(free, settings.options_per_offer, timezone)

    # --- Reserva ------------------------------------------------------------------

    def book(
        self, candidate: Candidate, slot: InterviewSlot, *, booked_by: str
    ) -> Interview | None:
        """Reserva y deja la entrevista en la ficha. `None` si está ocupado o ya no es válido.

        Al reagendar se reserva el nuevo antes de liberar el anterior.
        """
        if not self.enabled_for(candidate):
            return None
        assert candidate.country is not None
        slots, _, _ = self._valid_slots(candidate.country)
        if slot.start not in {s.start for s in slots}:
            return None
        country = candidate.country
        new_id = make_slot_id(country.value, slot.start)
        previous = candidate.interview
        if previous is not None and previous.slot_id == new_id:
            return previous
        now = self._clock.now()
        if not self._store.try_reserve(new_id, candidate.id, start=slot.start, now=now):
            return None
        if previous is not None:
            self._store.release(previous.slot_id, candidate.id)
        spec = self.country_settings(country)
        interview = Interview(
            slot_id=new_id,
            start=slot.start,
            end=slot.end,
            timezone=self.timezone(country),
            modality=spec.modality,
            location=self.location(candidate),
            booked_at=now,
            booked_by=booked_by,
        )
        candidate.interview = interview
        return interview

    def cancel(self, candidate: Candidate) -> Interview | None:
        previous = candidate.interview
        if previous is None:
            return None
        self._store.release(previous.slot_id, candidate.id)
        candidate.interview = None
        return previous

    # --- Textos -------------------------------------------------------------------

    def option_lines(self, candidate: Candidate, slots: list[InterviewSlot]) -> list[str]:
        assert candidate.country is not None
        return format_options(slots, self.timezone(candidate.country), candidate.language)

    def interview_time(self, candidate: Candidate, interview: Interview) -> str:
        slot = InterviewSlot(start=interview.start, end=interview.end)
        return format_slot(slot, interview.timezone, candidate.language)

    def country_settings(self, country: Country) -> CountryScheduling:
        settings = self.settings
        assert settings is not None
        return settings.countries[country]

    def location(self, candidate: Candidate) -> str | None:
        assert candidate.country is not None
        spec = self.country_settings(candidate.country)
        if spec.modality != InterviewModality.IN_PERSON:
            return None
        city = self.city(candidate)
        return spec.locations.get(city) if city else None

    def city(self, candidate: Candidate) -> str | None:
        """Nombre canónico de la ciudad de servicio (los alias, p. ej. CDMX, cuentan)."""
        assert candidate.country is not None
        wanted = normalize_text(candidate.data.city or candidate.application.city or "")
        for city in self._config.regions[candidate.country].cities:
            if any(normalize_text(n) == wanted for n in (city.name, *city.aliases)):
                return city.name
        return None

    # --- Internos -----------------------------------------------------------------

    def _valid_slots(self, country: Country) -> tuple[list[InterviewSlot], datetime, datetime]:
        settings = self.settings
        if settings is None or country not in settings.countries:
            now = self._clock.now()
            return [], now, now
        start = self._clock.now() + timedelta(hours=settings.min_notice_hours)
        end = start + timedelta(days=settings.horizon_days)
        slots = generate_slots(
            settings.countries[country],
            self.timezone(country),
            slot_minutes=settings.slot_minutes,
            start=start,
            end=end,
        )
        return slots, start, end
