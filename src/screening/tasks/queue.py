"""Cola de trabajo diferido (spec 016): interfaz, configuración y cola local.

En producción, `CloudTasksQueue`; en desarrollo y tests, `LocalTaskQueue`, que corre la
tarea dentro del proceso y por eso no sirve con varias instancias.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal, Protocol, Self

import httpx
from pydantic import BaseModel, SecretStr, model_validator

logger = logging.getLogger(__name__)

TASK_TOKEN_HEADER = "X-Task-Token"

Sleep = Callable[[float], Awaitable[None]]
AsgiApp = Callable[..., Awaitable[None]]


class TaskQueue(Protocol):
    def enqueue(
        self, path: str, payload: dict[str, Any], *, delay_seconds: float, task_id: str
    ) -> bool:
        """Programa un POST JSON a `path` dentro de `delay_seconds`.

        Devuelve `False` si ya existía una tarea con ese `task_id`. Lanza si la cola falla.
        """
        ...


class TaskQueueSettings(BaseModel):
    backend: Literal["local", "cloud"] = "local"
    token: SecretStr | None = None
    project: str | None = None
    location: str = "europe-west1"
    queue: str = "inbound-batches"
    service_url: str | None = None
    service_account: str | None = None

    @model_validator(mode="after")
    def _cloud_requires_target(self) -> Self:
        if self.backend == "cloud":
            missing = [
                name
                for name, value in (
                    ("TASKS_TOKEN", self.token),
                    ("CLOUD_TASKS_PROJECT", self.project),
                    ("TASKS_SERVICE_URL", self.service_url),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"TASKS_BACKEND=cloud requiere {', '.join(missing)}")
        return self

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> TaskQueueSettings:
        values: dict[str, Any] = {
            "backend": (env.get("TASKS_BACKEND") or "local").lower(),
            "token": SecretStr(env["TASKS_TOKEN"]) if env.get("TASKS_TOKEN") else None,
            "project": env.get("CLOUD_TASKS_PROJECT") or None,
            "location": env.get("CLOUD_TASKS_LOCATION") or None,
            "queue": env.get("CLOUD_TASKS_QUEUE") or None,
            "service_url": env.get("TASKS_SERVICE_URL") or None,
            "service_account": env.get("CLOUD_TASKS_SERVICE_ACCOUNT") or None,
        }
        return cls.model_validate({k: v for k, v in values.items() if v is not None})


class LocalTaskQueue:
    """Ejecuta cada tarea en el proceso: duerme el retraso y hace el POST a la app ASGI.

    Imita a Cloud Tasks: deduplica por `task_id` y reintenta con backoff si la respuesta no
    es 2xx. Tiene temporizadores en memoria: solo para desarrollo y tests.
    """

    def __init__(
        self, *, sleep: Sleep = asyncio.sleep, max_attempts: int = 5, backoff_seconds: float = 1.0
    ) -> None:
        self._sleep = sleep
        self._max_attempts = max_attempts
        self._backoff = backoff_seconds
        self._app: AsgiApp | None = None
        self._token = ""
        self._seen: set[str] = set()
        self._pending: dict[str, tuple[str, dict[str, Any]]] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def attach(self, app: AsgiApp, token: SecretStr) -> None:
        """La app que recibe las tareas y el token que exige su endpoint."""
        self._app = app
        self._token = token.get_secret_value()

    def enqueue(
        self, path: str, payload: dict[str, Any], *, delay_seconds: float, task_id: str
    ) -> bool:
        """Requiere un event loop en curso (se llama desde el webhook)."""
        if task_id in self._seen:
            return False
        self._seen.add(task_id)
        self._pending[task_id] = (path, payload)
        task = asyncio.get_running_loop().create_task(self._run(task_id, delay_seconds))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    async def aclose(self) -> None:
        """Cancela las esperas y ejecuta una vez, de inmediato, las tareas pendientes."""
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for task_id in list(self._pending):
            await self._post(task_id)
        self._pending.clear()

    async def _run(self, task_id: str, delay_seconds: float) -> None:
        await self._sleep(delay_seconds)
        for attempt in range(self._max_attempts):
            if await self._post(task_id):
                break
            if attempt + 1 < self._max_attempts:
                await self._sleep(self._backoff * 2**attempt)
        else:
            logger.warning("Tarea local %s agotó sus %d intentos", task_id, self._max_attempts)
        self._pending.pop(task_id, None)

    async def _post(self, task_id: str) -> bool:
        if self._app is None:
            raise RuntimeError("LocalTaskQueue sin app: falta llamar a attach()")
        path, payload = self._pending[task_id]
        transport = httpx.ASGITransport(app=self._app)
        async with httpx.AsyncClient(transport=transport, base_url="http://local-tasks") as client:
            try:
                response = await client.post(
                    path, json=payload, headers={TASK_TOKEN_HEADER: self._token}
                )
            except Exception:
                logger.exception("Tarea local %s falló", task_id)
                return False
        return response.is_success
