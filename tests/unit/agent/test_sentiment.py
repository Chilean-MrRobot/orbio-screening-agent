from datetime import date

import pytest

from screening.adapters.fake_llm import FakeLLMClient
from screening.agent import AdkScreeningAgent
from screening.agent.context import build_turn_instructions
from screening.agent.sentiment import (
    DEFAULT_PROMPT_PATH,
    SentimentClassifier,
    SentimentResponse,
)
from screening.clock import FixedClock
from screening.config import AppConfig
from screening.domain import Candidate, ChannelName, Message, MessageRole, Sentiment, Stage
from screening.ports import LLMError, ReplyRequest
from tests.unit.agent.fakes import system_text
from tests.unit.agent.test_extraction import candidate_says, llm_output

MODEL = "gpt-5-nano"


def classifier(*responses: object, **kwargs: object) -> tuple[SentimentClassifier, FakeLLMClient]:
    llm = FakeLLMClient(responses)
    return SentimentClassifier(llm, model=MODEL, **kwargs), llm


def says(label: str, confidence: float = 0.9) -> dict[str, object]:
    return {"label": label, "confidence": confidence}


# --- Clasificador -------------------------------------------------------------------


@pytest.mark.parametrize("label", list(Sentiment))
def test_classify_returns_each_label_with_high_confidence(label: Sentiment) -> None:
    sentiment, _ = classifier(says(label.value))

    result = sentiment.classify(["mensaje"])

    assert result.label == label
    assert result.confidence == 0.9
    assert [u.model for u in result.usage] == [MODEL]


def test_low_confidence_has_no_label_but_keeps_usage() -> None:
    sentiment, _ = classifier(says("frustrated", 0.3), min_confidence=0.6)

    result = sentiment.classify(["ok"])

    assert result.label is None
    assert result.confidence == 0.3
    assert len(result.usage) == 1


@pytest.mark.parametrize(
    "response", [LLMError("se cayó OpenAI"), RuntimeError("bug"), {"label": "angry"}]
)
def test_failures_have_no_label_and_do_not_raise(response: object) -> None:
    sentiment, _ = classifier(response)

    result = sentiment.classify(["hola"])

    assert result.label is None
    assert result.usage == []


def test_sends_only_the_last_messages_with_the_configured_model_and_prompt() -> None:
    sentiment, llm = classifier(says("rushed"), message_limit=3)

    sentiment.classify(["uno", "dos", " ", "tres", "cuatro", "cinco"])

    [call] = llm.calls
    assert call.model == MODEL
    assert call.schema is SentimentResponse
    assert "frustrated" in call.system and "{{" not in call.system
    [message] = call.messages
    assert message.role == "user"
    assert "uno" not in message.content and "dos" not in message.content
    assert message.content.splitlines()[-3:] == ["- tres", "- cuatro", "- cinco"]


def test_without_text_the_model_is_not_called() -> None:
    sentiment, llm = classifier()

    result = sentiment.classify(["", "  "])

    assert result.label is None
    assert llm.calls == []


def test_confidence_out_of_range_is_invalid() -> None:
    with pytest.raises(ValueError):
        SentimentResponse.model_validate(says("neutral", 1.5))


def test_repo_prompt_is_versioned_and_short() -> None:
    text = DEFAULT_PROMPT_PATH.read_text(encoding="utf-8")

    assert text.startswith("---\nversion:")
    assert len(text) < 2000  # entrada barata: el prompt va en cada mensaje


def test_from_config_uses_the_classifier_model(app_config: AppConfig) -> None:
    sentiment = SentimentClassifier.from_config(app_config, FakeLLMClient())

    assert sentiment.model == app_config.llm.classifier_model


# --- Integración con el agente ------------------------------------------------------


@pytest.mark.parametrize("label", list(Sentiment))
def test_classifier_label_replaces_extraction_heuristic(
    make_agent, candidate: Candidate, label: Sentiment
) -> None:
    sentiment, _ = classifier(says(label.value))
    heuristic = Sentiment.CONFUSED if label != Sentiment.CONFUSED else Sentiment.RUSHED
    raw = llm_output() | {"sentiment": heuristic.value}
    agent, _, _ = make_agent(extraction=[raw], sentiment_classifier=sentiment)

    outcome = agent.extract(candidate, candidate_says(candidate, "vale"), [])

    assert outcome.sentiment == label


def test_low_confidence_keeps_heuristic_and_sums_usage(make_agent, candidate: Candidate) -> None:
    sentiment, _ = classifier(says("frustrated", 0.2))
    raw = llm_output(city="Madrid") | {"sentiment": "rushed"}
    agent, _, _ = make_agent(extraction=[raw], sentiment_classifier=sentiment)

    outcome = agent.extract(candidate, candidate_says(candidate, "Madrid, rápido"), ["city"])

    assert outcome.sentiment == Sentiment.RUSHED
    assert outcome.updates.city == "Madrid"
    assert [(u.model, u.input_tokens, u.output_tokens) for u in outcome.usage] == [
        ("gpt-5-mini", 100, 20),
        (MODEL, 10, 5),
    ]


def test_classifier_failure_keeps_heuristic(make_agent, candidate: Candidate) -> None:
    sentiment, _ = classifier(LLMError("timeout"))
    raw = llm_output() | {"sentiment": "confused"}
    agent, _, _ = make_agent(extraction=[raw], sentiment_classifier=sentiment)

    outcome = agent.extract(candidate, candidate_says(candidate, "¿qué?"), [])

    assert outcome.sentiment == Sentiment.CONFUSED
    assert [u.model for u in outcome.usage] == ["gpt-5-mini"]


def test_usage_adds_classifier_to_extraction(make_agent, candidate: Candidate) -> None:
    sentiment, _ = classifier(says("frustrated"))
    agent, _, _ = make_agent(extraction=[llm_output()], sentiment_classifier=sentiment)

    outcome = agent.extract(candidate, candidate_says(candidate, "OTRA VEZ???"), [])

    assert [u.model for u in outcome.usage] == ["gpt-5-mini", MODEL]
    assert sum(u.input_tokens for u in outcome.usage) == 110


def test_classifier_gets_recent_candidate_messages_only(make_agent, candidate: Candidate) -> None:
    sentiment, llm = classifier(says("frustrated"))
    agent, _, _ = make_agent(extraction=[llm_output()], sentiment_classifier=sentiment)
    for text in ("hola", "sí"):
        candidate_says(candidate, text)
    candidate.messages.append(
        Message(role=MessageRole.AGENT, text="¿Tienes carnet?", channel=ChannelName.WHATSAPP)
    )

    agent.extract(candidate, candidate_says(candidate, "ya te lo dije", "B"), [])

    content = llm.calls[0].messages[0].content
    assert content.splitlines()[-3:] == ["- sí", "- ya te lo dije", "- B"]
    assert "hola" not in content and "carnet" not in content


def test_without_classifier_extraction_heuristic_is_kept(make_agent, candidate: Candidate) -> None:
    agent, _, _ = make_agent(extraction=[llm_output() | {"sentiment": "rushed"}])

    outcome = agent.extract(candidate, candidate_says(candidate, "rápido porfa"), [])

    assert outcome.sentiment == Sentiment.RUSHED
    assert len(outcome.usage) == 1


def test_without_text_the_classifier_is_not_called(make_agent, candidate: Candidate) -> None:
    sentiment, llm = classifier()
    agent, _, _ = make_agent(sentiment_classifier=sentiment)

    agent.extract(candidate, candidate_says(candidate, "  "), [])

    assert llm.calls == []


def test_from_env_wires_classifier_when_api_key(
    monkeypatch: pytest.MonkeyPatch,
    app_config: AppConfig,
    clock: FixedClock,
    knowledge,
    pricing,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    agent = AdkScreeningAgent.from_env(
        app_config, knowledge, clock=clock, pricing=pricing, tracing=False
    )
    assert agent.sentiment_classifier is not None
    assert agent.sentiment_classifier.model == app_config.llm.classifier_model

    monkeypatch.delenv("OPENAI_API_KEY")
    agent = AdkScreeningAgent.from_env(
        app_config, knowledge, clock=clock, pricing=pricing, tracing=False
    )
    assert agent.sentiment_classifier is None


# --- Tono de la respuesta -----------------------------------------------------------


def _instructions(candidate: Candidate, sentiment: Sentiment, **kwargs: object) -> str:
    candidate.sentiment = sentiment
    request = ReplyRequest(candidate=candidate, stage=Stage.KNOCKOUT, ask_fields=["city"])
    for key, value in kwargs.items():
        setattr(candidate, key, value)
    return build_turn_instructions(request, today=date(2026, 10, 23))


@pytest.mark.parametrize(
    ("sentiment", "expected"),
    [
        (Sentiment.FRUSTRATED, "ofrece hablar con una persona"),
        (Sentiment.CONFUSED, "ejemplo de respuesta"),
        (Sentiment.RUSHED, "directo al grano"),
    ],
)
def test_turn_instructions_add_tone_per_label(
    candidate: Candidate, sentiment: Sentiment, expected: str
) -> None:
    text = _instructions(candidate, sentiment)

    assert "- Tono:" in text
    assert expected in text


def test_neutral_has_no_tone_line(candidate: Candidate) -> None:
    assert "- Tono:" not in _instructions(candidate, Sentiment.NEUTRAL)


def test_frustrated_who_already_asked_for_a_person_is_not_offered_again(
    candidate: Candidate,
) -> None:
    text = _instructions(candidate, Sentiment.FRUSTRATED, human_requested=True)

    assert "ofrece hablar con una persona" not in text
    assert "confírmale" in text


def test_reply_instruction_sent_to_the_model_changes_with_label(
    make_agent, candidate: Candidate
) -> None:
    agent, _, model = make_agent(reply=["¿En qué ciudad?", "Perdona. ¿En qué ciudad?"])
    for sentiment in (Sentiment.NEUTRAL, Sentiment.FRUSTRATED):
        candidate.sentiment = sentiment
        agent.reply(ReplyRequest(candidate=candidate, stage=Stage.KNOCKOUT, ask_fields=["city"]))

    neutral, frustrated = (system_text(r) for r in model.requests)
    assert neutral != frustrated
    assert "- Tono: está molesto" in frustrated
    assert "- Tono:" not in neutral
