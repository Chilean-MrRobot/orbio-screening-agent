"""Traza por turno en LangSmith (vista Threads), probada con un `trace` falso."""

from __future__ import annotations

from typing import Any

import pytest

from screening.adapters.memory import InMemoryCandidateRepository
from screening.conversation import ConversationOrchestrator
from screening.conversation.tracing import TurnTrace, trace_turn, tracing_enabled
from screening.domain import Candidate, CandidateStatus, MessageRole
from screening.ports import ExtractionOutcome
from tests.unit.conversation.conftest import Inbox, ScriptedAgent


class FakeRun:
    def __init__(self) -> None:
        self.outputs: dict[str, Any] | None = None

    def end(self, outputs: dict[str, Any]) -> None:
        self.outputs = outputs


class FakeTrace:
    """Reemplaza `langsmith.trace`: registra cada traza abierta y cerrada."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.runs: list[FakeRun] = []
        self.exits: list[type[BaseException] | None] = []

    def __call__(self, **kwargs: Any) -> FakeTrace:
        self.calls.append(kwargs)
        return self

    def __enter__(self) -> FakeRun:
        run = FakeRun()
        self.runs.append(run)
        return run

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.exits.append(exc_type)
        return False


@pytest.fixture
def fake_trace(monkeypatch: pytest.MonkeyPatch) -> FakeTrace:
    import langsmith

    fake = FakeTrace()
    monkeypatch.setattr(langsmith, "trace", fake)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-test")
    monkeypatch.setenv("LANGSMITH_PROJECT", "orbio-test")
    return fake


def test_tracing_needs_flag_and_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    assert not tracing_enabled()
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-test")
    assert tracing_enabled()
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    assert not tracing_enabled()


def test_outputs_only_show_changed_fields_and_new_replies(candidate: Candidate, clock) -> None:
    candidate.data.city = "Madrid"
    turn = TurnTrace(candidate, {"mensaje": "x"})

    candidate.data.consent = True
    candidate.data.city = "Madrid"  # sin cambio: no aparece
    from screening.domain import ChannelName, Message

    candidate.messages.append(
        Message(role=MessageRole.AGENT, text="¿Tienes carnet?", channel=ChannelName.WEB)
    )

    out = turn.outputs()
    assert out["datos_nuevos"] == {"consent": True}
    assert out["respuesta"] == "¿Tienes carnet?"
    assert out["estado"] == candidate.status.value
    assert "puntaje" not in out


def test_disabled_tracing_does_not_call_langsmith(
    candidate: Candidate, monkeypatch: pytest.MonkeyPatch
) -> None:
    import langsmith

    def boom(**kwargs: Any) -> None:
        raise AssertionError("no debería llamarse")

    monkeypatch.setattr(langsmith, "trace", boom)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")

    with trace_turn("turno", candidate, {"mensaje": "hola"}):
        pass


def test_trace_has_thread_metadata_and_outputs(candidate: Candidate, fake_trace: FakeTrace) -> None:
    with trace_turn("turno", candidate, {"mensaje": "hola"}):
        candidate.data.consent = True

    call = fake_trace.calls[0]
    assert call["name"] == "turno"
    assert call["inputs"] == {"mensaje": "hola"}
    assert call["metadata"]["thread_id"] == candidate.id
    assert call["project_name"] == "orbio-test"
    assert fake_trace.runs[0].outputs is not None
    assert fake_trace.runs[0].outputs["datos_nuevos"] == {"consent": True}
    assert fake_trace.exits == [None]


def test_langsmith_failure_never_breaks_the_turn(
    candidate: Candidate, monkeypatch: pytest.MonkeyPatch
) -> None:
    import langsmith

    def broken(**kwargs: Any) -> None:
        raise RuntimeError("LangSmith caído")

    monkeypatch.setattr(langsmith, "trace", broken)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-test")
    ran = []

    with trace_turn("turno", candidate, {"mensaje": "hola"}):
        ran.append(True)

    assert ran == [True]


def test_errors_inside_the_turn_propagate_and_close_the_trace(
    candidate: Candidate, fake_trace: FakeTrace
) -> None:
    with pytest.raises(ValueError), trace_turn("turno", candidate, {"mensaje": "hola"}):
        raise ValueError("boom")

    assert fake_trace.exits == [ValueError]
    assert fake_trace.runs[0].outputs is not None


def test_orchestrator_traces_application_and_each_turn(
    contacted: Candidate,
    orchestrator: ConversationOrchestrator,
    agent: ScriptedAgent,
    inbox: Inbox,
    repository: InMemoryCandidateRepository,
    fake_trace: FakeTrace,
) -> None:
    agent.queue(ExtractionOutcome(provided_fields=["consent"], updates={"consent": True}))

    orchestrator.handle(inbox.batch("sí, acepto"))

    call = fake_trace.calls[-1]
    assert call["name"] == "turno"
    assert call["inputs"]["mensaje"] == "sí, acepto"
    assert call["metadata"]["thread_id"] == contacted.id
    outputs = fake_trace.runs[-1].outputs
    assert outputs is not None
    assert outputs["datos_nuevos"] == {"consent": True}
    assert outputs["estado"] == CandidateStatus.IN_SCREENING.value
    assert outputs["respuesta"]
