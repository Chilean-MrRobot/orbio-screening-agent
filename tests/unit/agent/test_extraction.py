from datetime import date
from typing import Any

import pytest
from google.adk.models.llm_response import LlmResponse

from screening.agent.extraction import (
    CONFIRMATION_FIELD,
    SLOT_FIELD,
    ExtractionResponse,
    build_outcome,
)
from screening.domain import (
    Candidate,
    ChannelName,
    Country,
    DeliveryExperience,
    Language,
    Message,
    MessageRole,
    ScreeningData,
    Sentiment,
    VehicleType,
)
from screening.ports import LLMError
from tests.unit.agent.fakes import contents, system_text

ES_LICENSES = ["AM", "A1", "A2", "A", "B"]
MX_LICENSES = ["A", "B", "motociclista"]

EMPTY_FIELDS: dict[str, Any] = {
    "full_name": None,
    "consent": None,
    "has_license": None,
    "license_type": None,
    "license_years": None,
    "vehicle_type": None,
    "owns_vehicle": None,
    "city": None,
    "availability": None,
    "preferred_schedule": None,
    "delivery_experience": None,
    "start_date": None,
    "work_permit": None,
}


def llm_output(invalid: list[str] | None = None, **fields: Any) -> dict[str, Any]:
    """Lo que devolvería el modelo de extracción."""
    return {
        "fields": {**EMPTY_FIELDS, **fields},
        "invalid_fields": invalid or [],
        "language": "es",
        "sentiment": "neutral",
        "opt_out": False,
        "human_requested": False,
        "question": None,
        "summary_confirmed": False,
        "slot_choice": None,
        "slots_rejected": False,
    }


def outcome_of(licenses: list[str] | None = ES_LICENSES, **kwargs: Any):
    return build_outcome(ExtractionResponse.model_validate(llm_output(**kwargs)), licenses=licenses)


def confirmed(expected_fields: list[str], **fields: Any):
    raw = llm_output(**fields) | {"summary_confirmed": True}
    return build_outcome(ExtractionResponse.model_validate(raw), expected_fields=expected_fields)


def candidate_says(candidate: Candidate, *texts: str) -> list[Message]:
    new = [Message(role=MessageRole.CANDIDATE, text=t, channel=ChannelName.WHATSAPP) for t in texts]
    candidate.messages.extend(new)
    return new


# --- Validación y normalización ---------------------------------------------------


def test_normalizes_license_code_and_lists_provided_fields() -> None:
    outcome = outcome_of(has_license=True, license_type="b", license_years=5)

    assert outcome.updates.license_type == "B"
    assert outcome.updates.license_years == 5
    assert outcome.provided_fields == ["has_license", "license_type", "license_years"]
    assert outcome.invalid_fields == []


def test_license_outside_country_table_is_invalid() -> None:
    outcome = outcome_of(license_type="Z", has_license=True)

    assert outcome.updates.license_type is None
    assert outcome.invalid_fields == ["license_type"]
    assert outcome.provided_fields == ["has_license"]


def test_license_table_is_per_country() -> None:
    assert outcome_of(licenses=MX_LICENSES, license_type="Motociclista").updates.license_type == (
        "motociclista"
    )
    assert outcome_of(licenses=ES_LICENSES, license_type="motociclista").invalid_fields == [
        "license_type"
    ]


def test_value_that_fails_domain_validation_becomes_invalid() -> None:
    outcome = outcome_of(license_years=-2, delivery_experience={"years": -1, "platforms": []})

    assert outcome.provided_fields == []
    assert outcome.invalid_fields == ["license_years", "delivery_experience"]


@pytest.mark.parametrize("raw", ["2026-13-40", "pronto"])
def test_malformed_start_date_is_invalid(raw: str) -> None:
    outcome = outcome_of(start_date=raw)

    assert outcome.updates.start_date is None
    assert outcome.invalid_fields == ["start_date"]


def test_valid_values_are_typed() -> None:
    outcome = outcome_of(
        start_date="2026-10-26",
        vehicle_type="motorcycle",
        delivery_experience={"years": 2, "platforms": ["Glovo"]},
    )

    assert outcome.updates.start_date == date(2026, 10, 26)
    assert outcome.updates.vehicle_type == VehicleType.MOTORCYCLE
    assert outcome.updates.delivery_experience == DeliveryExperience(years=2, platforms=["Glovo"])


def test_invalid_fields_drop_unknown_names_and_fields_with_value() -> None:
    outcome = outcome_of(invalid=["city", "nope", "needs_review", "start_date"], city="Madrid")

    assert outcome.invalid_fields == ["start_date"]
    assert outcome.provided_fields == ["city"]


def test_blank_strings_are_ignored() -> None:
    outcome = outcome_of(city="  ", full_name=" Ana Ruiz ")

    assert outcome.provided_fields == ["full_name"]
    assert outcome.updates.full_name == "Ana Ruiz"


def test_signals_are_copied_and_blank_question_is_none() -> None:
    raw = llm_output() | {
        "language": "en",
        "sentiment": "rushed",
        "opt_out": True,
        "human_requested": True,
        "question": "  ",
    }

    outcome = build_outcome(ExtractionResponse.model_validate(raw))

    assert outcome.language == Language.EN
    assert outcome.sentiment == Sentiment.RUSHED
    assert outcome.opt_out and outcome.human_requested
    assert outcome.question is None


def test_summary_confirmation_is_a_provided_field_when_expected() -> None:
    assert confirmed([CONFIRMATION_FIELD]).provided_fields == [CONFIRMATION_FIELD]


def test_summary_confirmation_is_ignored_when_not_expected_or_with_corrections() -> None:
    assert confirmed(["city"]).provided_fields == []
    assert confirmed([CONFIRMATION_FIELD], city="Sevilla").provided_fields == ["city"]


def test_repeated_unchanged_fields_do_not_cancel_the_confirmation() -> None:
    """Regresión (prueba de punta a punta): al confirmar, el modelo repitió datos del resumen."""
    current = ScreeningData(city="Madrid", license_type="B", has_license=True)
    raw = llm_output(city="Madrid", license_type="b", has_license=True) | {
        "summary_confirmed": True
    }

    outcome = build_outcome(
        ExtractionResponse.model_validate(raw),
        licenses=ES_LICENSES,
        expected_fields=[CONFIRMATION_FIELD],
        current=current,
    )

    assert outcome.provided_fields == [CONFIRMATION_FIELD]
    assert outcome.updates == ScreeningData()


def test_changed_field_is_still_a_correction_with_current_data() -> None:
    current = ScreeningData(city="Madrid", has_license=True)
    raw = llm_output(city="Sevilla", has_license=True) | {"summary_confirmed": True}

    outcome = build_outcome(
        ExtractionResponse.model_validate(raw),
        expected_fields=[CONFIRMATION_FIELD],
        current=current,
    )

    assert outcome.provided_fields == ["city"]


# --- Turno de extracción con ADK ----------------------------------------------------


def test_extract_runs_the_agent_with_context_and_schema(make_agent, candidate: Candidate) -> None:
    agent, model, _ = make_agent(
        extraction=[llm_output(has_license=True, license_type="B", license_years=5)]
    )
    candidate.messages.append(
        Message(role=MessageRole.AGENT, text="¿Tienes carnet?", channel=ChannelName.WHATSAPP)
    )
    new = candidate_says(candidate, "tengo el carnet de coche hace 5 años")

    outcome = agent.extract(candidate, new, ["has_license"])

    assert outcome.updates.license_type == "B"
    assert outcome.provided_fields == ["has_license", "license_type", "license_years"]
    request = model.requests[0]
    assert request.config.response_schema is ExtractionResponse
    assert "Eres Sofía" in system_text(request)
    assert "Solo cuenta la experiencia relevante" in system_text(request)
    [(role, text)] = contents(request)  # sin historial de ADK: todo va en un mensaje
    assert role == "user"
    assert "Fecha de hoy: 2026-10-22" in text
    assert "Sofía: ¿Tienes carnet?" in text
    assert text.endswith("- tengo el carnet de coche hace 5 años")


def test_extract_uses_region_of_candidate(make_agent, candidate: Candidate) -> None:
    candidate.country = Country.MX
    agent, model, _ = make_agent(extraction=[llm_output(license_type="A")])

    outcome = agent.extract(candidate, candidate_says(candidate, "licencia de carro"), [])

    assert outcome.updates.license_type == "A"
    assert "licencia de manejo" in system_text(model.requests[0])
    assert "motociclista (motorcycle)" in contents(model.requests[0])[0][1]


def test_extract_without_text_does_not_call_the_model(make_agent, candidate: Candidate) -> None:
    candidate.language = Language.EN
    agent, model, _ = make_agent()

    outcome = agent.extract(candidate, candidate_says(candidate, "  "), ["city"])

    assert model.requests == []
    assert outcome.language == Language.EN
    assert outcome.provided_fields == []


def test_invalid_output_is_retried_once_and_usage_sums_both(
    make_agent, candidate: Candidate
) -> None:
    agent, model, _ = make_agent(extraction=["esto no es JSON", llm_output(city="Madrid")])

    outcome = agent.extract(candidate, candidate_says(candidate, "Madrid"), ["city"])

    assert outcome.updates.city == "Madrid"
    assert len(model.requests) == 2
    assert len(outcome.usage) == 2


def test_json_in_code_fences_is_accepted(make_agent, candidate: Candidate) -> None:
    import json

    fenced = "```json\n" + json.dumps(llm_output(city="Sevilla")) + "\n```"
    agent, _, _ = make_agent(extraction=[fenced])

    assert agent.extract(candidate, candidate_says(candidate, "Sevilla"), []).updates.city == (
        "Sevilla"
    )


def test_two_invalid_outputs_raise_llm_error(make_agent, candidate: Candidate) -> None:
    agent, _, _ = make_agent(extraction=["{}", "nada"])

    with pytest.raises(LLMError, match="2 intentos"):
        agent.extract(candidate, candidate_says(candidate, "hola"), [])


def test_model_exception_raises_llm_error(make_agent, candidate: Candidate) -> None:
    agent, _, _ = make_agent(extraction=[RuntimeError("se cayó OpenAI")])

    with pytest.raises(LLMError, match="se cayó OpenAI"):
        agent.extract(candidate, candidate_says(candidate, "hola"), [])


def test_error_event_raises_llm_error(make_agent, candidate: Candidate) -> None:
    error = LlmResponse(error_code="RATE_LIMIT", error_message="demasiadas llamadas")
    agent, _, _ = make_agent(extraction=[error])

    with pytest.raises(LLMError, match="RATE_LIMIT"):
        agent.extract(candidate, candidate_says(candidate, "hola"), [])


# --- Agendamiento (spec 021) ------------------------------------------------------


def scheduling(expected_fields: list[str], **signals: Any):
    raw = llm_output() | signals
    return build_outcome(ExtractionResponse.model_validate(raw), expected_fields=expected_fields)


def test_slot_choice_is_kept_only_when_slots_were_offered() -> None:
    assert scheduling([SLOT_FIELD], slot_choice=2).slot_choice == 2
    assert scheduling(["city"], slot_choice=2).slot_choice is None
    assert scheduling([SLOT_FIELD], slot_choice=0).slot_choice is None


def test_slots_rejected_only_counts_without_a_choice() -> None:
    assert scheduling([SLOT_FIELD], slots_rejected=True).slots_rejected
    assert not scheduling([SLOT_FIELD], slots_rejected=True, slot_choice=1).slots_rejected
    assert not scheduling(["city"], slots_rejected=True).slots_rejected
