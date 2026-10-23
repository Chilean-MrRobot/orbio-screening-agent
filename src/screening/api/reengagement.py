"""`POST /tasks/reengagement`: pasada de calentamiento para Cloud Scheduler (spec 011)."""

from __future__ import annotations

import hmac
from typing import Protocol

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse
from pydantic import SecretStr

from screening.reengagement import ReengagementReport

REENGAGEMENT_PATH = "/tasks/reengagement"
TOKEN_HEADER = "X-Task-Token"


class ReengagementRunner(Protocol):
    def run(self) -> ReengagementReport: ...


def build_reengagement_router(runner: ReengagementRunner, token: SecretStr | None) -> APIRouter:
    """Sin token configurado la tarea queda deshabilitada (503), nunca abierta."""
    router = APIRouter()

    # `def` y no `async def`: la pasada es bloqueante y FastAPI la corre en un hilo.
    @router.post(REENGAGEMENT_PATH)
    def run(x_task_token: str | None = Header(default=None)) -> JSONResponse:
        if token is None:
            return JSONResponse({"error": "reengagement task disabled"}, status_code=503)
        expected = token.get_secret_value().encode()
        if x_task_token is None or not hmac.compare_digest(x_task_token.encode(), expected):
            return JSONResponse({"error": "invalid task token"}, status_code=401)
        return JSONResponse(runner.run().model_dump(mode="json"))

    return router
