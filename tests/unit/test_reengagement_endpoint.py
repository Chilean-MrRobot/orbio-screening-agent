from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from screening.api import WhatsAppSettings, create_app
from screening.api.reengagement import (
    REENGAGEMENT_PATH,
    TOKEN_HEADER,
    build_reengagement_router,
)
from screening.channels import InMemoryInboundMessageStore
from screening.clock import FixedClock
from screening.domain import ChannelName
from screening.reengagement import ReengagementReport, TouchOutcome, TouchRecord

TOKEN = "task-token"


class FakeRunner:
    def __init__(self) -> None:
        self.calls = 0

    def run(self) -> ReengagementReport:
        self.calls += 1
        touch = TouchRecord(
            candidate_id="c1",
            cadence="never_responded",
            step="reminder_whatsapp",
            channel=ChannelName.WHATSAPP,
            outcome=TouchOutcome.SENT,
        )
        return ReengagementReport(evaluated=1, touches=[touch])


def _client(runner: FakeRunner, token: str | None = TOKEN) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_reengagement_router(runner, SecretStr(token) if token is not None else None)
    )
    return TestClient(app)


def test_without_token_is_rejected() -> None:
    runner = FakeRunner()

    response = _client(runner).post(REENGAGEMENT_PATH)

    assert response.status_code == 401
    assert runner.calls == 0


def test_wrong_token_is_rejected() -> None:
    runner = FakeRunner()

    response = _client(runner).post(REENGAGEMENT_PATH, headers={TOKEN_HEADER: "nope"})

    assert response.status_code == 401
    assert runner.calls == 0


def test_right_token_runs_the_pass_and_returns_the_report() -> None:
    runner = FakeRunner()

    response = _client(runner).post(REENGAGEMENT_PATH, headers={TOKEN_HEADER: TOKEN})

    assert response.status_code == 200
    assert runner.calls == 1
    body = response.json()
    assert body["evaluated"] == 1
    assert body["touches"][0]["outcome"] == "sent"


def test_unconfigured_token_disables_the_task() -> None:
    runner = FakeRunner()

    response = _client(runner, token=None).post(REENGAGEMENT_PATH, headers={TOKEN_HEADER: ""})

    assert response.status_code == 503
    assert runner.calls == 0


def test_create_app_mounts_the_task(clock: FixedClock) -> None:
    class NoopHandler:
        def handle(self, batch: object) -> None: ...

    runner = FakeRunner()
    settings = WhatsAppSettings(verify_token=SecretStr("v"), app_secret=SecretStr("s"))
    app = create_app(
        settings,
        store=InMemoryInboundMessageStore(),
        handler=NoopHandler(),
        clock=clock,
        reengagement=runner,
        reengagement_token=SecretStr(TOKEN),
    )

    response = TestClient(app).post(REENGAGEMENT_PATH, headers={TOKEN_HEADER: TOKEN})

    assert response.status_code == 200
    assert runner.calls == 1
