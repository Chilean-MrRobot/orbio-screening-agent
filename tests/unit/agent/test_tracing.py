import logging
import sys
import types

import pytest

from screening.agent import adk_agent, tracing

MODULE = "langsmith.integrations.google_adk"


@pytest.fixture
def fake_langsmith(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []
    module = types.ModuleType(MODULE)

    def configure_google_adk(**kwargs):
        calls.append(kwargs)
        return True

    module.configure_google_adk = configure_google_adk
    monkeypatch.setitem(sys.modules, MODULE, module)
    return calls


def test_configure_tracing_uses_langsmith_project(
    fake_langsmith: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANGSMITH_PROJECT", "orbio-test")

    assert tracing.configure_tracing() is True
    assert fake_langsmith == [{"project_name": "orbio-test"}]


def test_configure_tracing_failure_only_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setitem(sys.modules, MODULE, None)  # el import falla

    with caplog.at_level(logging.WARNING):
        assert tracing.configure_tracing() is False
    assert "sin trazas" in caplog.text


@pytest.mark.parametrize(("env", "expected"), [("true", 1), ("false", 0), ("", 0)])
def test_agent_enables_tracing_from_env(
    make_agent, monkeypatch: pytest.MonkeyPatch, env: str, expected: int
) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(adk_agent, "configure_tracing", lambda: calls.append(True))
    monkeypatch.setenv("LANGSMITH_TRACING", env)

    make_agent(tracing=None)

    assert len(calls) == expected
