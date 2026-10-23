from screening.domain import Candidate, Language, ScreeningData, Stage, VehicleType
from screening.ports import ExtractionOutcome, LLMUsage, ReplyOutcome, ReplyRequest


def test_extraction_outcome_round_trips_with_usage() -> None:
    outcome = ExtractionOutcome(
        updates=ScreeningData(has_license=True, vehicle_type=VehicleType.MOTORCYCLE),
        provided_fields=["has_license", "vehicle_type"],
        language=Language.EN,
        usage=[LLMUsage("gpt-5-mini", 120, 30, 850.0, 0.0001)],
    )

    restored = ExtractionOutcome.model_validate_json(outcome.model_dump_json())

    assert restored == outcome
    assert restored.usage[0].input_tokens == 120


def test_extraction_outcome_defaults_are_neutral() -> None:
    outcome = ExtractionOutcome()

    assert outcome.updates == ScreeningData()
    assert not outcome.opt_out
    assert not outcome.human_requested
    assert outcome.question is None


def test_reply_request_carries_candidate_snapshot(candidate: Candidate) -> None:
    request = ReplyRequest(candidate=candidate, stage=Stage.KNOCKOUT, ask_fields=["has_license"])

    assert request.candidate.id == candidate.id
    assert ReplyOutcome(text="¿Tienes carnet?").usage == []
