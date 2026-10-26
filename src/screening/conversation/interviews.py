"""Variables de las plantillas del agendamiento (spec 021): horarios, lugar y zona horaria."""

from __future__ import annotations

from screening.config import AppConfig
from screening.conversation.messages import render_template
from screening.domain import Candidate, Interview, InterviewModality, InterviewSlot
from screening.scheduling import InterviewScheduler


def offer_variables(
    scheduler: InterviewScheduler,
    candidate: Candidate,
    config: AppConfig,
    slots: list[InterviewSlot],
) -> dict[str, str]:
    """Para `interview_offer*` y `interview_slot_taken`: la lista numerada en hora local."""
    settings = scheduler.settings
    assert settings is not None
    return {
        **_common(scheduler, candidate, config),
        "options": "\n".join(scheduler.option_lines(candidate, slots)),
        "duration": str(settings.slot_minutes),
    }


def confirmation_variables(
    scheduler: InterviewScheduler, candidate: Candidate, config: AppConfig, interview: Interview
) -> dict[str, str]:
    return {
        **_common(scheduler, candidate, config),
        "interview_time": scheduler.interview_time(candidate, interview),
    }


def _common(
    scheduler: InterviewScheduler, candidate: Candidate, config: AppConfig
) -> dict[str, str]:
    assert candidate.country is not None
    spec = scheduler.country_settings(candidate.country)
    city = scheduler.city(candidate) or candidate.data.city or ""
    address = scheduler.location(candidate)
    if spec.modality == InterviewModality.IN_PERSON and address:
        extra = {"city": city, "address": address}
        place = render_template("interview_place_in_person", candidate, config, extra)
    else:
        place = render_template("interview_place_video", candidate, config)
    return {
        "place": place,
        "timezone_label": spec.timezone_label[candidate.language],
        "city": city,
        "address": address or "",
    }
