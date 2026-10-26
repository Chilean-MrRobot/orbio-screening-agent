from datetime import UTC, date, datetime

from screening.agent.context import (
    NO_NEW_MESSAGES,
    build_extraction_input,
    build_turn_instructions,
    conversation,
    local_today,
    region_for,
    split_for_reply,
)
from screening.config import AppConfig
from screening.domain import (
    Candidate,
    ChannelName,
    Country,
    InterviewSlot,
    Language,
    Message,
    MessageRole,
    ScreeningData,
    Sentiment,
    Stage,
)
from screening.ports import ReplyRequest


def msg(role: MessageRole, text: str) -> Message:
    return Message(role=role, text=text, channel=ChannelName.WHATSAPP)


def test_local_today_uses_region_timezone() -> None:
    late_night_utc = datetime(2026, 10, 22, 23, 30, tzinfo=UTC)

    assert local_today(late_night_utc, "Europe/Madrid") == date(2026, 10, 23)
    assert local_today(late_night_utc, "America/Mexico_City") == date(2026, 10, 22)


def test_region_falls_back_to_application_country_then_first_region(
    candidate: Candidate, app_config: AppConfig
) -> None:
    candidate.country = None
    candidate.application.country = Country.MX
    assert region_for(candidate, app_config).country == Country.MX

    candidate.application.country = None
    assert region_for(candidate, app_config).country == Country.ES


def test_conversation_hides_system_messages_and_applies_limit() -> None:
    messages = [
        msg(MessageRole.AGENT, "uno"),
        msg(MessageRole.SYSTEM, "evento interno"),
        msg(MessageRole.CANDIDATE, "dos"),
        msg(MessageRole.AGENT, "tres"),
    ]

    assert [m.text for m in conversation(messages, 2)] == ["dos", "tres"]
    assert conversation(messages, 0) == []


def test_extraction_input_separates_history_from_new_messages(
    candidate: Candidate, app_config: AppConfig
) -> None:
    candidate.stage = Stage.KNOCKOUT
    candidate.data = ScreeningData(consent=True)
    new = [msg(MessageRole.CANDIDATE, "sí, tengo el B"), msg(MessageRole.CANDIDATE, "hace 5 años")]
    candidate.messages = [
        msg(MessageRole.AGENT, "¿Tienes carnet de conducir?"),
        msg(MessageRole.SYSTEM, "regla evaluada"),
        *new,
    ]

    text = build_extraction_input(
        candidate,
        new,
        ["has_license", "license_type"],
        region=app_config.regions[Country.ES],
        today=date(2026, 10, 23),
        agent_name="Sofía",
        history_limit=20,
    )

    history, news = text.split("Mensajes nuevos del candidato:")
    assert "Fecha de hoy: 2026-10-23 (viernes)" in history
    assert "Etapa: knockout" in history
    assert "Campos esperados en esta etapa: has_license, license_type" in history
    assert "B (car, motorcycle)" in history
    assert '"consent": true' in history
    assert "Sofía: ¿Tienes carnet de conducir?" in history
    assert "regla evaluada" not in text
    assert "sí, tengo el B" not in history
    assert news.strip().splitlines() == ["- sí, tengo el B", "- hace 5 años"]


def test_split_for_reply_leaves_pending_candidate_messages_apart() -> None:
    messages = [
        msg(MessageRole.CANDIDATE, "hola"),
        msg(MessageRole.AGENT, "¿Tienes carnet?"),
        msg(MessageRole.CANDIDATE, "sí"),
        msg(MessageRole.CANDIDATE, "el B"),
    ]

    history, pending = split_for_reply(messages, 20)

    assert [m.text for m in history] == ["hola", "¿Tienes carnet?"]
    assert [m.text for m in pending] == ["sí", "el B"]


def test_split_for_reply_without_agent_messages_is_all_pending() -> None:
    history, pending = split_for_reply([msg(MessageRole.CANDIDATE, "hola")], 20)

    assert history == []
    assert [m.text for m in pending] == ["hola"]
    assert NO_NEW_MESSAGES


def _request(candidate: Candidate, **kwargs) -> ReplyRequest:
    return ReplyRequest(candidate=candidate, stage=Stage.PROFILE, **kwargs)


def test_turn_instructions_retry_has_priority_over_ask(candidate: Candidate) -> None:
    text = build_turn_instructions(
        _request(candidate, ask_fields=["availability"], retry_fields=["start_date"]),
        today=date(2026, 10, 23),
    )

    assert "Repregunta con un ejemplo el dato `start_date`" in text
    assert "availability" not in text


def test_turn_instructions_ask_only_the_first_field(candidate: Candidate) -> None:
    text = build_turn_instructions(
        _request(candidate, ask_fields=["availability", "preferred_schedule"]),
        today=date(2026, 10, 23),
    )

    assert "Pregunta el dato `availability`" in text
    assert "preferred_schedule" not in text
    assert "Nombre de pila del candidato: Lucía" in text
    assert "Idioma del mensaje: español" in text


def test_turn_instructions_carry_question_mood_human_and_language(candidate: Candidate) -> None:
    candidate.language = Language.EN
    candidate.sentiment = Sentiment.FRUSTRATED
    candidate.human_requested = True

    text = build_turn_instructions(
        _request(candidate, question="¿Cuánto pagan?", ask_fields=["city"]),
        today=date(2026, 10, 23),
    )

    assert "Idioma del mensaje: inglés" in text
    assert "Estado de ánimo del candidato: frustrated" in text
    assert "pidió hablar con una persona" in text
    assert '"¿Cuánto pagan?"' in text
    assert "Pregunta el dato `city`" in text


def test_turn_instructions_closing_summary_lists_known_data(candidate: Candidate) -> None:
    candidate.data = ScreeningData(license_type="B", city="Madrid", needs_review=["start_date"])

    text = build_turn_instructions(
        _request(candidate, closing_summary=True), today=date(2026, 10, 23)
    )

    assert "resumen de cierre" in text
    assert '"license_type": "B"' in text
    assert "needs_review" not in text


def test_turn_instructions_with_nothing_to_ask(candidate: Candidate) -> None:
    text = build_turn_instructions(_request(candidate), today=date(2026, 10, 23))

    assert "No hay nada que preguntar" in text


# --- Agendamiento (spec 021) ------------------------------------------------------


def test_extraction_input_lists_offered_slots_in_local_time(
    candidate: Candidate, app_config: AppConfig
) -> None:
    candidate.stage = Stage.SCHEDULING
    candidate.scheduling.offered = [
        InterviewSlot(
            start=datetime(2026, 10, 26, 9, 0, tzinfo=UTC),
            end=datetime(2026, 10, 26, 9, 30, tzinfo=UTC),
        ),
        InterviewSlot(
            start=datetime(2026, 10, 27, 15, 0, tzinfo=UTC),
            end=datetime(2026, 10, 27, 15, 30, tzinfo=UTC),
        ),
    ]
    new = [msg(MessageRole.CANDIDATE, "el martes")]

    def build(expected: list[str]) -> str:
        return build_extraction_input(
            candidate,
            new,
            expected,
            region=app_config.regions[Country.ES],
            today=date(2026, 10, 23),
            agent_name="Sofía",
            history_limit=20,
        )

    text = build(["interview_slot"])
    assert (
        "Horarios ofrecidos (hora local): 1) 2026-10-26 (lunes) 10:00; 2) 2026-10-27 (martes) 16:00"
    ) in text
    assert "Horarios ofrecidos" not in build(["city"])


def test_turn_instructions_repeat_the_interview_options(candidate: Candidate) -> None:
    options = ["1) lunes 26 de octubre, 10:00", "2) martes 27 de octubre, 16:00"]

    text = build_turn_instructions(
        _request(candidate, question="¿Es presencial?", interview_options=options),
        today=date(2026, 10, 23),
    )

    assert '"¿Es presencial?"' in text
    assert "1) lunes 26 de octubre, 10:00; 2) martes 27 de octubre, 16:00" in text
    assert "No hay nada que preguntar" not in text
