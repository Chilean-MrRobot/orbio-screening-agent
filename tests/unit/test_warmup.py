"""Calentamiento de los LLM al arrancar la API (spec 022)."""

from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient

from screening.api.app import create_app
from screening.api.warmup import warm_up_llms, warmup_enabled, warmup_timeout
from screening.channels import InMemoryInboundMessageStore, LoggingInboundHandler
from screening.clock import FixedClock
from screening.config import AppConfig


def test_each_configured_model_is_called_once(app_config: AppConfig) -> None:
    seen: list[str] = []
    lock = threading.Lock()

    def complete(model: str) -> None:
        with lock:
            seen.append(model)

    timings = warm_up_llms(app_config, complete=complete)

    expected = {
        app_config.llm.extraction_model,
        app_config.llm.conversation_model,
        app_config.llm.classifier_model,
    }
    assert sorted(seen) == sorted(expected)
    assert set(timings) == expected


def test_errors_do_not_propagate(app_config: AppConfig) -> None:
    def complete(model: str) -> None:
        raise RuntimeError("openai caído")

    assert set(warm_up_llms(app_config, complete=complete)) == {
        app_config.llm.extraction_model,
        app_config.llm.conversation_model,
        app_config.llm.classifier_model,
    }


def test_enabled_by_default_with_key_and_configurable() -> None:
    assert warmup_enabled({"OPENAI_API_KEY": "k"}) is True
    assert warmup_enabled({}) is False
    assert warmup_enabled({"OPENAI_API_KEY": "k", "LLM_WARMUP": "false"}) is False
    assert warmup_timeout({}) == 8.0
    assert warmup_timeout({"LLM_WARMUP_TIMEOUT_SECONDS": "3"}) == 3.0


def test_startup_runs_the_warmup_without_waiting_past_the_timeout(clock: FixedClock) -> None:
    started = threading.Event()
    release = threading.Event()

    def warmup() -> None:
        started.set()
        release.wait(5)

    app = create_app(
        None,
        store=InMemoryInboundMessageStore(),
        handler=LoggingInboundHandler(),
        clock=clock,
        warmup=warmup,
        warmup_timeout=0.2,
    )

    begin = time.monotonic()
    with TestClient(app) as client:
        elapsed = time.monotonic() - begin
        assert client.get("/health").status_code == 200
        release.set()  # si no, el cierre espera al hilo del calentamiento

    assert started.is_set()
    assert elapsed < 2
