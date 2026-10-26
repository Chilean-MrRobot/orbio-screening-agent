from __future__ import annotations

import asyncio
import json
from datetime import UTC, timedelta
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from google.api_core.exceptions import AlreadyExists
from google.cloud import tasks_v2
from pydantic import SecretStr, ValidationError

from screening.clock import FixedClock
from screening.tasks import (
    TASK_TOKEN_HEADER,
    CloudTasksQueue,
    LocalTaskQueue,
    TaskQueueSettings,
)
from tests.unit.fake_timer import FakeTimer, eventually, run

PARENT = "projects/demo-project/locations/europe-west1/queues/inbound-batches"


class FakeCloudTasksClient:
    def __init__(self) -> None:
        self.created: list[tuple[str, dict[str, Any]]] = []
        self._names: set[str] = set()

    def create_task(self, *, parent: str, task: dict[str, Any]) -> dict[str, Any]:
        if task["name"] in self._names:
            raise AlreadyExists(f"{task['name']} ya existe")
        self._names.add(task["name"])
        self.created.append((parent, task))
        return task


def cloud_queue(
    client: FakeCloudTasksClient, clock: FixedClock, **overrides: Any
) -> CloudTasksQueue:
    settings = TaskQueueSettings(
        backend="cloud",
        token=SecretStr("tok"),
        project="demo-project",
        service_url="https://screening.run.app/",
        **overrides,
    )
    return CloudTasksQueue.from_settings(settings, clock, client=client)


# --- CloudTasksQueue -----------------------------------------------------------------


def test_cloud_queue_creates_http_task(clock: FixedClock) -> None:
    client = FakeCloudTasksClient()
    queue = cloud_queue(client, clock)

    created = queue.enqueue(
        "/tasks/process-inbound", {"sender": "+34600111222"}, delay_seconds=8, task_id="abc-1"
    )

    assert created
    [(parent, task)] = client.created
    assert parent == PARENT
    assert task["name"] == f"{PARENT}/tasks/abc-1"
    request = task["http_request"]
    assert request["http_method"] == tasks_v2.HttpMethod.POST
    assert request["url"] == "https://screening.run.app/tasks/process-inbound"
    assert request["headers"] == {"Content-Type": "application/json", TASK_TOKEN_HEADER: "tok"}
    assert json.loads(request["body"]) == {"sender": "+34600111222"}
    assert "oidc_token" not in request
    assert task["schedule_time"].ToDatetime(tzinfo=UTC) == clock.now() + timedelta(seconds=8)


def test_cloud_queue_repeated_task_id_is_not_an_error(clock: FixedClock) -> None:
    client = FakeCloudTasksClient()
    queue = cloud_queue(client, clock)

    assert queue.enqueue("/p", {}, delay_seconds=8, task_id="same")
    assert not queue.enqueue("/p", {}, delay_seconds=8, task_id="same")
    assert len(client.created) == 1


def test_cloud_queue_adds_oidc_token_when_configured(clock: FixedClock) -> None:
    client = FakeCloudTasksClient()
    queue = cloud_queue(client, clock, service_account="tasks@demo.iam.gserviceaccount.com")

    queue.enqueue("/p", {}, delay_seconds=0, task_id="t")

    request = client.created[0][1]["http_request"]
    assert request["oidc_token"] == {
        "service_account_email": "tasks@demo.iam.gserviceaccount.com",
        "audience": "https://screening.run.app",
    }


def test_cloud_queue_propagates_other_errors(clock: FixedClock) -> None:
    class BrokenClient(FakeCloudTasksClient):
        def create_task(self, *, parent: str, task: dict[str, Any]) -> dict[str, Any]:
            raise ConnectionError("cola caída")

    queue = cloud_queue(BrokenClient(), clock)

    with pytest.raises(ConnectionError):
        queue.enqueue("/p", {}, delay_seconds=0, task_id="t")


# --- Configuración -------------------------------------------------------------------


def test_settings_default_to_local() -> None:
    settings = TaskQueueSettings.from_env({})

    assert settings.backend == "local"
    assert settings.token is None
    assert settings.location == "europe-west1"


def test_settings_from_env_for_cloud() -> None:
    settings = TaskQueueSettings.from_env(
        {
            "TASKS_BACKEND": "cloud",
            "TASKS_TOKEN": "tok",
            "CLOUD_TASKS_PROJECT": "p",
            "CLOUD_TASKS_LOCATION": "europe-west1",
            "CLOUD_TASKS_QUEUE": "q",
            "TASKS_SERVICE_URL": "https://x.run.app",
            "CLOUD_TASKS_SERVICE_ACCOUNT": "sa@p.iam.gserviceaccount.com",
        }
    )

    assert settings.backend == "cloud"
    assert settings.queue == "q"
    assert settings.token is not None and settings.token.get_secret_value() == "tok"
    assert settings.service_account == "sa@p.iam.gserviceaccount.com"


@pytest.mark.parametrize("missing", ["TASKS_TOKEN", "CLOUD_TASKS_PROJECT", "TASKS_SERVICE_URL"])
def test_cloud_settings_require_token_project_and_url(missing: str) -> None:
    env = {
        "TASKS_BACKEND": "cloud",
        "TASKS_TOKEN": "tok",
        "CLOUD_TASKS_PROJECT": "p",
        "TASKS_SERVICE_URL": "https://x.run.app",
    }
    del env[missing]

    with pytest.raises(ValidationError, match=missing):
        TaskQueueSettings.from_env(env)


def test_unknown_backend_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TaskQueueSettings.from_env({"TASKS_BACKEND": "rabbit"})


# --- LocalTaskQueue ------------------------------------------------------------------


class TargetApp:
    """App ASGI de destino: registra cada llamada y responde los códigos programados."""

    def __init__(self, statuses: list[int] | None = None) -> None:
        self.calls: list[tuple[dict[str, Any], str | None]] = []
        self.statuses = statuses or []
        self.app = FastAPI()

        @self.app.post("/work")
        async def work(request: Request) -> JSONResponse:
            self.calls.append((await request.json(), request.headers.get(TASK_TOKEN_HEADER)))
            status = self.statuses.pop(0) if self.statuses else 200
            return JSONResponse({}, status_code=status)


def local_queue(target: TargetApp, timer: FakeTimer) -> LocalTaskQueue:
    queue = LocalTaskQueue(sleep=timer.sleep)
    queue.attach(target.app, SecretStr("local-token"))
    return queue


def test_local_queue_runs_task_after_delay(clock: FixedClock) -> None:
    timer, target = FakeTimer(clock), TargetApp()

    async def scenario() -> None:
        queue = local_queue(target, timer)
        assert queue.enqueue("/work", {"n": 1}, delay_seconds=8, task_id="a")
        await asyncio.sleep(0.01)  # que la tarea alcance a registrar su espera
        await timer.advance(7)
        assert target.calls == []
        await timer.advance(1)
        await eventually(lambda: len(target.calls) == 1)

    run(scenario())

    assert target.calls == [({"n": 1}, "local-token")]


def test_local_queue_deduplicates_task_id(clock: FixedClock) -> None:
    timer, target = FakeTimer(clock), TargetApp()

    async def scenario() -> None:
        queue = local_queue(target, timer)
        assert queue.enqueue("/work", {"n": 1}, delay_seconds=8, task_id="a")
        assert not queue.enqueue("/work", {"n": 2}, delay_seconds=8, task_id="a")
        await asyncio.sleep(0.01)
        await timer.advance(8)
        await eventually(lambda: len(target.calls) == 1)
        await timer.advance(60)

    run(scenario())

    assert [payload for payload, _ in target.calls] == [{"n": 1}]


def test_local_queue_retries_on_server_error(clock: FixedClock) -> None:
    timer, target = FakeTimer(clock), TargetApp(statuses=[503, 503, 200])

    async def scenario() -> None:
        queue = local_queue(target, timer)
        queue.enqueue("/work", {}, delay_seconds=0, task_id="a")
        await timer.advance(0)
        await eventually(lambda: len(target.calls) == 1 and timer.waiting == 1)
        await timer.advance(1)  # primer backoff
        await eventually(lambda: len(target.calls) == 2 and timer.waiting == 1)
        await timer.advance(2)  # segundo backoff
        await eventually(lambda: len(target.calls) == 3)
        await timer.advance(60)

    run(scenario())

    assert len(target.calls) == 3


def test_local_queue_runs_pending_tasks_on_close(clock: FixedClock) -> None:
    timer, target = FakeTimer(clock), TargetApp()

    async def scenario() -> None:
        queue = local_queue(target, timer)
        queue.enqueue("/work", {"n": 1}, delay_seconds=8, task_id="a")
        await asyncio.sleep(0.01)
        await queue.aclose()

    run(scenario())

    assert [payload for payload, _ in target.calls] == [{"n": 1}]
