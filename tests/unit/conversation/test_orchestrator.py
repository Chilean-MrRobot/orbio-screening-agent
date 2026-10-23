from __future__ import annotations

from datetime import date

import pytest

from screening.adapters.memory import InMemoryCandidateRepository
from screening.conversation import CONFIRMATION_FIELD, ConversationOrchestrator, usage_totals
from screening.domain import (
    Availability,
    Candidate,
    CandidateStatus,
    Country,
    DeliveryExperience,
    Language,
    MessageRole,
    Schedule,
    ScreeningData,
    Stage,
    VehicleType,
)
from screening.ports import ExtractionOutcome
from tests.unit.conversation.conftest import PHONE, USAGE, Inbox, RecordingChannel, ScriptedAgent

KNOCKOUT_OK = dict(
    has_license=True,
    license_type="B",
    vehicle_type=VehicleType.CAR,
    city="Madrid",
)
PROFILE_OK = dict(
    delivery_experience=DeliveryExperience(years=3, platforms=["Glovo"]),
    availability=Availability.FULL_TIME,
    start_date=date(2026, 10, 25),
    owns_vehicle=True,
    preferred_schedule=Schedule.FLEXIBLE,
    work_permit=True,
)


def extraction(**updates: object) -> ExtractionOutcome:
    data = ScreeningData(**updates)
    return ExtractionOutcome(
        updates=data, provided_fields=sorted(data.model_fields_set), usage=[USAGE]
    )


def saved(repository: InMemoryCandidateRepository) -> Candidate:
    candidate = repository.get_by_phone(PHONE)
    assert candidate is not None
    return candidate


def events(candidate: Candidate, type: str) -> list[dict[str, object]]:
    return [e.detail for e in candidate.timeline if e.type == type]


def advance_to_closing(
    orchestrator: ConversationOrchestrator, agent: ScriptedAgent, inbox: Inbox
) -> None:
    agent.queue(extraction(consent=True), extraction(**KNOCKOUT_OK), extraction(**PROFILE_OK))
    orchestrator.handle(inbox.batch("sí, acepto"))
    orchestrator.handle(inbox.batch("tengo carnet B, coche propio y vivo en Madrid"))
    orchestrator.handle(inbox.batch("3 años en Glovo, full time desde el sábado, flexible"))


# --- Conversación completa -------------------------------------------------------


def test_full_conversation_ends_qualified(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
) -> None:
    assert channel.texts[0].startswith("¡Hola Lucía! Soy Sofía")
    assert contacted.status == CandidateStatus.CONTACTED

    agent.queue(extraction(consent=True))
    orchestrator.handle(inbox.batch("sí, acepto"))
    candidate = saved(repository)
    assert candidate.status == CandidateStatus.IN_SCREENING
    assert candidate.stage == Stage.KNOCKOUT
    assert agent.extract_calls[0].expected_fields == ["consent"]
    assert agent.reply_calls[-1].ask_fields == [
        "has_license",
        "license_type",
        "vehicle_type",
        "city",
    ]

    agent.queue(extraction(**KNOCKOUT_OK, owns_vehicle=True))
    orchestrator.handle(inbox.batch("carnet B, coche propio, Madrid"))
    candidate = saved(repository)
    assert candidate.stage == Stage.PROFILE
    assert candidate.knockout is not None and candidate.knockout.passed
    assert "owns_vehicle" not in agent.reply_calls[-1].ask_fields

    agent.queue(extraction(**{k: v for k, v in PROFILE_OK.items() if k != "owns_vehicle"}))
    orchestrator.handle(inbox.batch("3 años en Glovo, full time, flexible"))
    candidate = saved(repository)
    assert candidate.stage == Stage.CLOSING
    assert agent.reply_calls[-1].closing_summary

    agent.queue(ExtractionOutcome(provided_fields=[CONFIRMATION_FIELD]))
    orchestrator.handle(inbox.batch("sí, todo correcto"))
    candidate = saved(repository)
    assert agent.extract_calls[-1].expected_fields == [CONFIRMATION_FIELD]
    assert candidate.status == CandidateStatus.QUALIFIED
    assert candidate.stage == Stage.DONE
    assert candidate.score is not None and candidate.score.total == 100
    assert channel.texts[-1].startswith("¡Listo, Lucía!")
    assert "48 h" in channel.texts[-1]
    assert len(agent.reply_calls) == 3


def test_closing_without_confirmation_asks_for_the_summary_again(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
) -> None:
    advance_to_closing(orchestrator, agent, inbox)

    agent.queue(ExtractionOutcome())
    orchestrator.handle(inbox.batch("mmm"))

    assert saved(repository).status == CandidateStatus.IN_SCREENING
    assert agent.reply_calls[-1].closing_summary


def test_score_uses_the_clock_date(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
    clock,
) -> None:
    advance_to_closing(orchestrator, agent, inbox)
    clock.advance(days=30)  # el inicio (25-oct) ya pasó: sigue contando como inmediato

    agent.queue(ExtractionOutcome(provided_fields=[CONFIRMATION_FIELD]))
    orchestrator.handle(inbox.batch("ok"))

    candidate = saved(repository)
    assert candidate.score is not None
    assert candidate.score.components["start_date"] == 20
    assert events(candidate, "score_computed")[0]["total"] == candidate.score.total


# --- Descartes -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "reason", "text_start"),
    [
        ({"consent": False}, "no_consent", "Entendido, Lucía. Sin tu autorización"),
        ({"consent": True, "has_license": False}, "no_license", "Gracias por contarme, Lucía"),
        (
            {
                "consent": True,
                "has_license": True,
                "license_type": "AM",
                "vehicle_type": VehicleType.CAR,
            },
            "license_vehicle_mismatch",
            "Gracias, Lucía. Tu carnet de conducir no habilita",
        ),
        ({"consent": True, "city": "Lugo"}, "out_of_service_area", "Gracias, Lucía. Por ahora"),
    ],
)
def test_failed_rule_disqualifies_with_its_template(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
    answer: dict[str, object],
    reason: str,
    text_start: str,
) -> None:
    agent.queue(extraction(**answer))
    orchestrator.handle(inbox.batch("respuesta"))

    candidate = saved(repository)
    assert candidate.status == CandidateStatus.DISQUALIFIED
    assert candidate.stage == Stage.DONE
    assert candidate.knockout is not None and candidate.knockout.reason_code == reason
    assert events(candidate, "status_changed")[-1]["reason"] == reason
    assert channel.texts[-1].startswith(text_start)
    assert agent.reply_calls == []


def test_disqualification_uses_regional_vocabulary(
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
) -> None:
    mexican = "+525512345678"
    orchestrator.start_application(
        {
            "application_id": "app-mx",
            "source": "occ",
            "full_name": "Juan López",
            "phone": mexican,
            "country": "MX",
        }
    )
    agent.queue(extraction(consent=True, has_license=False))
    orchestrator.handle(inbox.batch("no tengo", sender=mexican))

    assert "licencia de manejo" in channel.texts[-1]


def test_disqualification_message_follows_the_candidate_language(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    inbox: Inbox,
) -> None:
    outcome = extraction(consent=True, has_license=False)
    outcome.language = Language.EN
    agent.queue(outcome)
    orchestrator.handle(inbox.batch("I don't have a license"))

    assert channel.texts[-1].startswith("Thanks for letting me know, Lucía")


def test_correction_reruns_rules_and_can_disqualify(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
) -> None:
    advance_to_closing(orchestrator, agent, inbox)

    agent.queue(extraction(city="Lugo"))
    orchestrator.handle(inbox.batch("perdón, en realidad vivo en Lugo"))

    candidate = saved(repository)
    assert candidate.data.city == "Lugo"
    assert candidate.status == CandidateStatus.DISQUALIFIED
    assert events(candidate, "field_updated")[-1] == {
        "field": "city",
        "value": "Lugo",
        "previous": "Madrid",
    }


def test_correction_in_closing_asks_for_a_new_summary(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
) -> None:
    advance_to_closing(orchestrator, agent, inbox)

    outcome = extraction(city="Barcelona")
    outcome.provided_fields.append(CONFIRMATION_FIELD)
    agent.queue(outcome)
    orchestrator.handle(inbox.batch("todo bien, pero vivo en Barcelona"))

    candidate = saved(repository)
    assert candidate.data.city == "Barcelona"
    assert candidate.status == CandidateStatus.IN_SCREENING
    assert candidate.stage == Stage.CLOSING
    assert agent.reply_calls[-1].closing_summary
    assert events(candidate, "knockouts_evaluated")[-1]["failed_rule"] is None


def test_correction_can_reopen_a_previous_stage(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
) -> None:
    advance_to_closing(orchestrator, agent, inbox)

    agent.queue(extraction(vehicle_type=VehicleType.MOTORCYCLE))
    orchestrator.handle(inbox.batch("mejor reparto en moto"))

    assert saved(repository).stage == Stage.KNOCKOUT
    assert agent.reply_calls[-1].ask_fields == ["license_years"]


# --- Opt-out y humano ------------------------------------------------------------


def test_opt_out_stops_all_messages(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
) -> None:
    sent_before = len(channel.sent)
    agent.queue(ExtractionOutcome(opt_out=True))
    orchestrator.handle(inbox.batch("no me escriban más"))
    orchestrator.handle(inbox.batch("hola?"))

    candidate = saved(repository)
    assert candidate.status == CandidateStatus.OPTED_OUT
    assert len(channel.sent) == sent_before
    assert len(agent.extract_calls) == 1
    assert len([m for m in candidate.messages if m.role == MessageRole.CANDIDATE]) == 2


def test_human_request_is_flagged_and_acknowledged(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
) -> None:
    agent.queue(ExtractionOutcome(human_requested=True))
    orchestrator.handle(inbox.batch("quiero hablar con una persona"))

    candidate = saved(repository)
    assert candidate.human_requested
    assert events(candidate, "human_requested")
    assert "persona" in channel.texts[-1]
    assert agent.reply_calls == []


# --- Inválidos y preguntas -------------------------------------------------------


def test_invalid_field_is_retried_then_marked_for_review(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
) -> None:
    agent.queue(
        extraction(consent=True),
        ExtractionOutcome(invalid_fields=["has_license"]),
        ExtractionOutcome(invalid_fields=["has_license"]),
    )
    orchestrator.handle(inbox.batch("sí"))

    orchestrator.handle(inbox.batch("depende"))
    assert agent.reply_calls[-1].retry_fields == ["has_license"]
    assert "has_license" not in agent.reply_calls[-1].ask_fields

    orchestrator.handle(inbox.batch("quizás"))
    candidate = saved(repository)
    assert candidate.data.needs_review == ["has_license"]
    assert agent.reply_calls[-1].retry_fields == []
    assert agent.reply_calls[-1].ask_fields[0] == "license_type"
    assert [e["attempt"] for e in events(candidate, "field_invalid")] == [1, 2]
    assert events(candidate, "field_needs_review") == [{"field": "has_license"}]


def test_valid_answer_clears_needs_review(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
) -> None:
    agent.queue(
        extraction(consent=True),
        ExtractionOutcome(invalid_fields=["city"]),
        ExtractionOutcome(invalid_fields=["city"]),
        extraction(city="Madrid"),
    )
    for text in ("sí", "por ahí", "cerca", "Madrid"):
        orchestrator.handle(inbox.batch(text))

    assert saved(repository).data.needs_review == []


def test_question_is_passed_to_the_reply_with_pending_fields(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
) -> None:
    agent.queue(ExtractionOutcome(question="¿Cuánto se paga por pedido?"))
    orchestrator.handle(inbox.batch("¿cuánto pagan?"))

    request = agent.reply_calls[-1]
    assert request.question == "¿Cuánto se paga por pedido?"
    assert request.ask_fields == ["consent"]
    assert events(saved(repository), "question_received")


# --- Errores ---------------------------------------------------------------------


def test_extract_error_sends_apology_and_keeps_messages(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
) -> None:
    agent.queue(RuntimeError("LLM caído"))
    orchestrator.handle(inbox.batch("hola"))

    candidate = saved(repository)
    assert events(candidate, "agent_error")[0]["turn"] == "extract"
    assert candidate.messages[-2].text == "hola"
    assert "problema" in channel.texts[-1]


def test_reply_error_sends_apology(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
) -> None:
    agent.fail_reply = True
    agent.queue(extraction(consent=True))
    orchestrator.handle(inbox.batch("sí"))

    candidate = saved(repository)
    assert candidate.data.consent is True
    assert events(candidate, "agent_error")[0]["turn"] == "reply"
    assert "problema" in channel.texts[-1]


@pytest.mark.parametrize("mode", ["fail", "raise_error"])
def test_channel_error_is_recorded_without_crashing(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
    mode: str,
) -> None:
    setattr(channel, mode, True)
    agent.queue(extraction(consent=True))
    orchestrator.handle(inbox.batch("sí"))

    candidate = saved(repository)
    failures = events(candidate, "message_failed")
    assert [f["template"] for f in failures] == [None, "apology"]
    assert candidate.data.consent is True
    assert candidate.messages[-1].role == MessageRole.CANDIDATE


def test_unexpected_error_does_not_escape(
    contacted: Candidate,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
    clock,
    app_config,
) -> None:
    class BrokenRegistry:
        def get(self, name: str):
            raise RuntimeError("registro roto")

    orchestrator = ConversationOrchestrator(
        repository=repository,
        agent=agent,
        channel=channel,
        clock=clock,
        config=app_config,
        registry=BrokenRegistry(),  # type: ignore[arg-type]
    )
    agent.queue(extraction(consent=True))
    orchestrator.handle(inbox.batch("sí"))

    candidate = saved(repository)
    assert events(candidate, "orchestrator_error")
    assert "problema" in channel.texts[-1]


# --- Persistencia y timeline -----------------------------------------------------


class SnapshotRepository(InMemoryCandidateRepository):
    def __init__(self) -> None:
        super().__init__()
        self.snapshots: list[Candidate] = []

    def save(self, candidate: Candidate) -> None:
        super().save(candidate)
        self.snapshots.append(candidate.model_copy(deep=True))


def test_candidate_is_saved_after_each_step(
    agent: ScriptedAgent, channel: RecordingChannel, inbox: Inbox, clock, app_config
) -> None:
    repository = SnapshotRepository()
    orchestrator = ConversationOrchestrator(
        repository=repository, agent=agent, channel=channel, clock=clock, config=app_config
    )
    orchestrator.start_application(
        {"application_id": "a", "source": "s", "full_name": "Lucía", "phone": PHONE}
    )
    repository.snapshots.clear()

    agent.queue(extraction(consent=True))
    orchestrator.handle(inbox.batch("sí"))

    first, last = repository.snapshots[0], repository.snapshots[-1]
    assert len(repository.snapshots) >= 3
    assert first.messages[-1].text == "sí" and first.data.consent is None
    types = [e.type for e in last.timeline]
    for expected in (
        "message_received",
        "status_changed",
        "llm_usage",
        "field_updated",
        "knockouts_evaluated",
        "stage_changed",
        "message_sent",
    ):
        assert expected in types
    assert last.messages[-1].role == MessageRole.AGENT
    assert last.last_agent_message_at == clock.now()


def test_usage_is_accumulated_in_the_timeline(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
) -> None:
    agent.queue(extraction(consent=True))
    orchestrator.handle(inbox.batch("sí"))

    totals = usage_totals(saved(repository))
    assert totals.calls == 2  # extract + reply
    assert totals.input_tokens == 200
    assert totals.output_tokens == 40
    assert totals.cost_usd == pytest.approx(0.0004)
    usage_events = events(saved(repository), "llm_usage")
    assert {e["turn"] for e in usage_events} == {"extract", "reply"}


def test_unknown_sender_gets_a_minimal_candidate(
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
) -> None:
    orchestrator.handle(inbox.batch("hola, quiero trabajar", profile_name="Pedro"))

    candidate = saved(repository)
    assert candidate.application.source == "whatsapp"
    assert candidate.application.full_name == "Pedro"
    assert candidate.country == Country.ES
    assert candidate.status == CandidateStatus.IN_SCREENING
    assert agent.extract_calls[0].expected_fields == ["full_name", "consent"]
    assert events(candidate, "candidate_created")


def test_reprocessing_the_same_batch_is_idempotent(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
) -> None:
    batch = inbox.batch("sí")
    agent.queue(extraction(consent=True))
    orchestrator.handle(batch)
    orchestrator.handle(batch)

    assert len(agent.extract_calls) == 1
    assert len([m for m in saved(repository).messages if m.text == "sí"]) == 1


def test_messages_after_close_are_stored_but_not_answered(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    channel: RecordingChannel,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
) -> None:
    agent.queue(extraction(consent=False))
    orchestrator.handle(inbox.batch("no"))
    sent_before = len(channel.sent)

    orchestrator.handle(inbox.batch("¿y ahora?"))

    candidate = saved(repository)
    assert len(channel.sent) == sent_before
    assert events(candidate, "message_after_close")
    assert len(agent.extract_calls) == 1


def test_no_response_candidate_resumes_screening(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
    clock,
) -> None:
    candidate = saved(repository)
    candidate.set_status(CandidateStatus.NO_RESPONSE, clock.now())
    repository.save(candidate)

    agent.queue(extraction(consent=True))
    orchestrator.handle(inbox.batch("perdón, sí sigo interesada"))

    assert saved(repository).status == CandidateStatus.IN_SCREENING
