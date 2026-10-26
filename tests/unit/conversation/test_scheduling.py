"""Agendamiento en la conversación (spec 021): calificado → horarios → entrevista."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from screening.adapters.memory import InMemoryCandidateRepository, InMemoryInterviewSlotStore
from screening.channels import SimulatedSmsChannel
from screening.clock import FixedClock
from screening.config import AppConfig
from screening.conversation import CONFIRMATION_FIELD, ConversationOrchestrator
from screening.domain import (
    Candidate,
    CandidateStatus,
    ChannelName,
    Country,
    InterviewModality,
    Language,
    Stage,
    VehicleType,
)
from screening.ports import ExtractionOutcome
from screening.reengagement import run_due_touches
from screening.scheduling import InterviewScheduler, slot_id
from tests.unit.conversation.conftest import (
    APPLICATION,
    Inbox,
    RecordingChannel,
    ScriptedAgent,
    without_scheduling,
)
from tests.unit.conversation.test_orchestrator import (
    PROFILE_OK,
    advance_to_closing,
    events,
    extraction,
    saved,
)

CONFIRM = ExtractionOutcome(provided_fields=[CONFIRMATION_FIELD])


@pytest.fixture
def store() -> InMemoryInterviewSlotStore:
    return InMemoryInterviewSlotStore()


@pytest.fixture
def scheduling_config(app_config: AppConfig) -> AppConfig:
    return app_config


@pytest.fixture
def orchestrator(
    repository: InMemoryCandidateRepository,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    clock: FixedClock,
    scheduling_config: AppConfig,
    store: InMemoryInterviewSlotStore,
) -> ConversationOrchestrator:
    scheduler = InterviewScheduler(scheduling_config, store, clock)
    return ConversationOrchestrator(
        repository=repository,
        agent=agent,
        channel=channel,
        clock=clock,
        config=scheduling_config,
        scheduler=scheduler,
    )


def qualify(
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    inbox: Inbox,
    confirm: ExtractionOutcome = CONFIRM,
) -> None:
    advance_to_closing(orchestrator, agent, inbox)
    agent.queue(confirm)
    orchestrator.handle(inbox.batch("sí, todo correcto"))


def answer(
    orchestrator: ConversationOrchestrator, agent: ScriptedAgent, inbox: Inbox, text: str, **kw
) -> None:
    agent.queue(ExtractionOutcome(**kw))
    orchestrator.handle(inbox.batch(text))


# --- Oferta ------------------------------------------------------------------------


def test_qualified_candidate_gets_three_options_instead_of_the_end(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
) -> None:
    qualify(orchestrator, agent, inbox)

    candidate = saved(repository)
    assert candidate.status == CandidateStatus.QUALIFIED
    assert candidate.stage == Stage.SCHEDULING
    assert candidate.score is not None and candidate.score.total == 100
    assert len(candidate.scheduling.offered) == 3
    offer = channel.texts[-1]
    assert offer.startswith("¡Listo, Lucía! 🎉 Tu postulación quedó completa.")
    assert "entrevista de 30 min por videollamada" in offer
    assert "(hora peninsular)" in offer
    # Jueves 10:04 UTC + 24 h → viernes 12:04 en Madrid; luego lunes y martes.
    assert (
        "1) viernes 23 de octubre, 12:30\n"
        "2) lunes 26 de octubre, 10:00\n"
        "3) martes 27 de octubre, 10:00"
    ) in offer
    assert events(candidate, "interview_offered")[0]["template"] == "interview_offer"
    # Espera la respuesta: queda agendado el calentamiento.
    assert candidate.reengagement.next_touch_at is not None


def test_offer_follows_the_candidate_language(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    inbox: Inbox,
) -> None:
    confirm = ExtractionOutcome(provided_fields=[CONFIRMATION_FIELD], language=Language.EN)

    qualify(orchestrator, agent, inbox, confirm)

    assert channel.texts[-1].startswith("All set, Lucía!")
    assert "1) Friday, October 23, 12:30" in channel.texts[-1]
    assert "(Spain mainland time)" in channel.texts[-1]


# --- Elección ----------------------------------------------------------------------


def test_choosing_an_option_books_the_interview(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
    store: InMemoryInterviewSlotStore,
) -> None:
    qualify(orchestrator, agent, inbox)

    answer(orchestrator, agent, inbox, "el segundo", slot_choice=2)

    assert agent.extract_calls[-1].expected_fields == ["interview_slot"]
    candidate = saved(repository)
    assert candidate.status == CandidateStatus.INTERVIEW
    assert candidate.stage == Stage.DONE
    interview = candidate.interview
    assert interview is not None
    assert interview.start == datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
    assert interview.modality == InterviewModality.VIDEO and interview.booked_by == "agent"
    assert store.owner(interview.slot_id) == candidate.id
    assert candidate.scheduling.offered == []
    assert channel.texts[-1] == (
        "¡Agendado, Lucía! ✅ Tu entrevista es el lunes 26 de octubre, 10:00 (hora peninsular), "
        "por videollamada (te enviaremos el enlace antes de la entrevista). "
        "Si necesitas cambiarla, escríbenos por aquí."
    )
    assert candidate.reengagement.next_touch_at is None


def test_a_slot_taken_meanwhile_offers_new_options(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
    store: InMemoryInterviewSlotStore,
    clock: FixedClock,
) -> None:
    qualify(orchestrator, agent, inbox)
    taken = saved(repository).scheduling.offered[0]
    store.try_reserve(slot_id("ES", taken.start), "otro", start=taken.start, now=clock.now())

    answer(orchestrator, agent, inbox, "el primero", slot_choice=1)

    candidate = saved(repository)
    assert candidate.status == CandidateStatus.QUALIFIED
    assert candidate.stage == Stage.SCHEDULING
    assert candidate.interview is None
    assert taken.start not in {s.start for s in candidate.scheduling.offered}
    assert candidate.scheduling.rejections == 0
    assert channel.texts[-1].startswith("Uy, Lucía, ese horario se acaba de ocupar.")


def test_none_fits_twice_leaves_it_for_the_recruiter(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
) -> None:
    qualify(orchestrator, agent, inbox)
    first = saved(repository).scheduling.offered

    answer(orchestrator, agent, inbox, "ninguno me sirve", slots_rejected=True)

    candidate = saved(repository)
    assert candidate.stage == Stage.SCHEDULING
    second = candidate.scheduling.offered
    assert len(second) == 3
    assert {s.start for s in second}.isdisjoint(s.start for s in first)
    assert channel.texts[-1].startswith("Sin problema, Lucía. Te propongo otros horarios")

    answer(orchestrator, agent, inbox, "tampoco puedo", slots_rejected=True)

    candidate = saved(repository)
    assert candidate.status == CandidateStatus.QUALIFIED
    assert candidate.stage == Stage.DONE
    assert candidate.interview is None
    assert candidate.scheduling.manual_required
    assert candidate.scheduling.manual_reason == "declined_options"
    assert candidate.scheduling.rejections == 2
    assert "te escribirá en las próximas 48 h para coordinar" in channel.texts[-1]
    assert candidate.reengagement.next_touch_at is None


def test_a_question_is_answered_and_the_options_are_repeated(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
) -> None:
    qualify(orchestrator, agent, inbox)

    answer(orchestrator, agent, inbox, "¿es presencial?", question="¿La entrevista es presencial?")

    request = agent.reply_calls[-1]
    assert request.stage == Stage.SCHEDULING
    assert request.question == "¿La entrevista es presencial?"
    assert request.interview_options[0] == "1) viernes 23 de octubre, 12:30"
    assert len(request.interview_options) == 3
    assert saved(repository).stage == Stage.SCHEDULING


def test_an_unclear_answer_repeats_the_same_options(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
) -> None:
    qualify(orchestrator, agent, inbox)
    offered = saved(repository).scheduling.offered

    answer(orchestrator, agent, inbox, "mmm")

    assert saved(repository).scheduling.offered == offered
    assert channel.texts[-1].startswith("Perdona, no me quedó claro cuál prefieres")
    assert "1) viernes 23 de octubre, 12:30" in channel.texts[-1]


def test_an_out_of_range_choice_counts_as_unclear(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
) -> None:
    qualify(orchestrator, agent, inbox)

    answer(orchestrator, agent, inbox, "el quinto", slot_choice=5)

    assert saved(repository).interview is None
    assert channel.texts[-1].startswith("Perdona, no me quedó claro")


def test_asking_for_a_human_leaves_it_for_the_recruiter(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
) -> None:
    qualify(orchestrator, agent, inbox)

    answer(orchestrator, agent, inbox, "prefiero hablar con alguien", human_requested=True)

    candidate = saved(repository)
    assert candidate.human_requested
    assert candidate.stage == Stage.DONE and candidate.status == CandidateStatus.QUALIFIED
    assert candidate.scheduling.manual_reason == "human_requested"
    assert channel.texts[-1].startswith("Entendido, Lucía. Le aviso al equipo")


def test_opt_out_while_scheduling(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
) -> None:
    qualify(orchestrator, agent, inbox)

    answer(orchestrator, agent, inbox, "ya no me interesa", opt_out=True)

    candidate = saved(repository)
    assert candidate.status == CandidateStatus.OPTED_OUT
    assert candidate.scheduling.offered == []


def test_a_message_after_booking_is_only_logged(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
) -> None:
    qualify(orchestrator, agent, inbox)
    answer(orchestrator, agent, inbox, "el 3", slot_choice=3)
    sent = len(channel.texts)

    orchestrator.handle(inbox.batch("gracias!"))

    assert len(channel.texts) == sent
    assert events(saved(repository), "message_after_close")


# --- Sin horarios, desactivado y México ---------------------------------------------


@pytest.fixture
def sundays_only(app_config: AppConfig) -> AppConfig:
    """Solo domingos y un día de horizonte: desde el jueves no hay ningún horario."""
    assert app_config.scheduling is not None
    scheduling = app_config.scheduling
    countries = dict(scheduling.countries)
    countries[Country.ES] = countries[Country.ES].model_copy(update={"days": [7]})
    update = {"countries": countries, "horizon_days": 1}
    return app_config.model_copy(update={"scheduling": scheduling.model_copy(update=update)})


def test_without_free_slots_it_closes_as_before_and_flags_manual(
    sundays_only: AppConfig,
    repository: InMemoryCandidateRepository,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    clock: FixedClock,
    store: InMemoryInterviewSlotStore,
    inbox: Inbox,
) -> None:
    orchestrator = ConversationOrchestrator(
        repository=repository,
        agent=agent,
        channel=channel,
        clock=clock,
        config=sundays_only,
        scheduler=InterviewScheduler(sundays_only, store, clock),
    )
    orchestrator.start_application(dict(APPLICATION))

    qualify(orchestrator, agent, inbox)

    candidate = saved(repository)
    assert candidate.status == CandidateStatus.QUALIFIED and candidate.stage == Stage.DONE
    assert candidate.scheduling.manual_reason == "no_slots"
    assert channel.texts[-1].startswith("¡Listo, Lucía! 🎉 Tu postulación quedó completa. Un")


def test_disabled_scheduling_keeps_the_previous_flow(
    app_config: AppConfig,
    repository: InMemoryCandidateRepository,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    clock: FixedClock,
    store: InMemoryInterviewSlotStore,
    inbox: Inbox,
) -> None:
    config = without_scheduling(app_config)
    orchestrator = ConversationOrchestrator(
        repository=repository,
        agent=agent,
        channel=channel,
        clock=clock,
        config=config,
        scheduler=InterviewScheduler(config, store, clock),
    )
    orchestrator.start_application(dict(APPLICATION))

    qualify(orchestrator, agent, inbox)

    candidate = saved(repository)
    assert candidate.status == CandidateStatus.QUALIFIED and candidate.stage == Stage.DONE
    assert candidate.scheduling.offered == [] and not candidate.scheduling.manual_required
    assert channel.texts[-1].startswith("¡Listo, Lucía! 🎉 Tu postulación quedó completa. Un")
    assert all("interview_slot" not in c.expected_fields for c in agent.extract_calls)


def test_mexico_offers_in_person_interviews_in_local_time(
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
) -> None:
    phone = "+525511112222"
    application = {**APPLICATION, "phone": phone, "country": "MX", "city": "CDMX"}
    orchestrator.start_application(application)
    knockout = dict(has_license=True, license_type="A", vehicle_type=VehicleType.CAR, city="CDMX")
    agent.queue(
        extraction(consent=True),
        extraction(**knockout),
        extraction(**PROFILE_OK),
        CONFIRM,
        ExtractionOutcome(slot_choice=1),
    )
    for text in ("sí", "licencia A, carro, CDMX", "perfil", "sí, todo bien"):
        orchestrator.handle(inbox.batch(text, sender=phone))

    offer = channel.texts[-1]
    assert "en persona en nuestro local de Ciudad de México" in offer
    assert "(Av. Insurgentes Sur 1235, col. Del Valle)" in offer
    assert "(hora del centro de México)" in offer
    assert "1) viernes 23 de octubre, 10:00" in offer

    orchestrator.handle(inbox.batch("el primero", sender=phone))

    candidate = repository.get_by_phone(phone)
    assert candidate is not None and candidate.interview is not None
    assert candidate.interview.start == datetime(2026, 10, 23, 16, 0, tzinfo=UTC)
    assert candidate.interview.location == "Av. Insurgentes Sur 1235, col. Del Valle"


# --- Silencio: el calentamiento existente -------------------------------------------


def test_silence_uses_the_cadence_and_ends_in_manual_scheduling(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
    clock: FixedClock,
    scheduling_config: AppConfig,
) -> None:
    qualify(orchestrator, agent, inbox)
    channels = {ChannelName.WHATSAPP: channel, ChannelName.SMS: SimulatedSmsChannel(clock)}

    for _ in range(6):
        clock.advance(days=1)
        run_due_touches(
            repository=repository, channels=channels, clock=clock, config=scheduling_config
        )

    candidate = saved(repository)
    sent = [e["template"] for e in events(candidate, "reengagement_touch_sent")]
    assert sent == ["interview_reminder", "interview_reminder_sms", "interview_no_response"]
    assert candidate.status == CandidateStatus.QUALIFIED
    assert candidate.stage == Stage.DONE
    assert candidate.scheduling.manual_reason == "no_response"
    assert channel.texts[-1].startswith("Lucía, como no tuvimos respuesta, un reclutador")
    assert candidate.reengagement.next_touch_at is None


def test_replying_after_a_reminder_still_books(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
    clock: FixedClock,
    scheduling_config: AppConfig,
) -> None:
    qualify(orchestrator, agent, inbox)
    channels = {ChannelName.WHATSAPP: channel, ChannelName.SMS: SimulatedSmsChannel(clock)}
    clock.advance(hours=25)
    run_due_touches(repository=repository, channels=channels, clock=clock, config=scheduling_config)
    assert channel.texts[-1].startswith("¡Hola de nuevo Lucía! Solo falta elegir")

    # Ya pasó un día: el viernes 12:30 quedó a menos de 24 h y no se puede reservar.
    answer(orchestrator, agent, inbox, "el primero", slot_choice=1)
    assert saved(repository).interview is None
    assert channel.texts[-1].startswith("Uy, Lucía, ese horario se acaba de ocupar.")

    answer(orchestrator, agent, inbox, "el primero", slot_choice=1)

    candidate = saved(repository)
    assert candidate.status == CandidateStatus.INTERVIEW
    assert candidate.interview is not None
    assert candidate.interview.start >= clock.now() + timedelta(hours=24)
