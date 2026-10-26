"""`TaskQueue` sobre Google Cloud Tasks: una tarea HTTP hacia el propio servicio (spec 016)."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Protocol

from google.api_core.exceptions import AlreadyExists
from google.cloud import tasks_v2
from google.protobuf import timestamp_pb2

from screening.clock import Clock
from screening.tasks.queue import TASK_TOKEN_HEADER, TaskQueueSettings


class CloudTasksClient(Protocol):
    """Lo que usamos de `tasks_v2.CloudTasksClient` (en los tests, un fake)."""

    def create_task(self, *, parent: str, task: dict[str, Any]) -> Any: ...


class CloudTasksQueue:
    def __init__(
        self,
        client: CloudTasksClient,
        *,
        project: str,
        location: str,
        queue: str,
        service_url: str,
        token: str,
        clock: Clock,
        service_account: str | None = None,
    ) -> None:
        self._client = client
        self._parent = f"projects/{project}/locations/{location}/queues/{queue}"
        self._service_url = service_url.rstrip("/")
        self._token = token
        self._clock = clock
        self._service_account = service_account

    @classmethod
    def from_settings(
        cls, settings: TaskQueueSettings, clock: Clock, client: CloudTasksClient | None = None
    ) -> CloudTasksQueue:
        assert settings.token and settings.project and settings.service_url  # validado
        return cls(
            client or tasks_v2.CloudTasksClient(),
            project=settings.project,
            location=settings.location,
            queue=settings.queue,
            service_url=settings.service_url,
            token=settings.token.get_secret_value(),
            clock=clock,
            service_account=settings.service_account,
        )

    def enqueue(
        self, path: str, payload: dict[str, Any], *, delay_seconds: float, task_id: str
    ) -> bool:
        http_request: dict[str, Any] = {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": self._service_url + path,
            "headers": {"Content-Type": "application/json", TASK_TOKEN_HEADER: self._token},
            "body": json.dumps(payload).encode(),
        }
        if self._service_account:
            http_request["oidc_token"] = {
                "service_account_email": self._service_account,
                "audience": self._service_url,
            }
        schedule_time = timestamp_pb2.Timestamp()
        schedule_time.FromDatetime(self._clock.now() + timedelta(seconds=delay_seconds))
        task = {
            "name": f"{self._parent}/tasks/{task_id}",
            "http_request": http_request,
            "schedule_time": schedule_time,
        }
        try:
            self._client.create_task(parent=self._parent, task=task)
        except AlreadyExists:
            # Cloud Tasks rechaza nombres repetidos: ya hay una tarea para esta ventana.
            return False
        return True
