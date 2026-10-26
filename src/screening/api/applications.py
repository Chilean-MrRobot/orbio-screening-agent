"""`POST /applications`: entrada del JSON del portal y primer contacto (spec 008)."""

from __future__ import annotations

import json
from typing import Any, Protocol

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from screening.conversation import ApplicationStartResult, CandidateBusy
from screening.intake import IntakeOutcome

APPLICATIONS_PATH = "/applications"
BUSY_RETRY_AFTER_SECONDS = 10

_STATUS_CODES = {
    IntakeOutcome.CREATED: 201,
    IntakeOutcome.DUPLICATE: 200,
    IntakeOutcome.INVALID_DATA: 200,
    IntakeOutcome.REJECTED: 422,
}


class ApplicationStarter(Protocol):
    def start_application(self, raw: dict[str, Any]) -> ApplicationStartResult: ...


def build_applications_router(starter: ApplicationStarter) -> APIRouter:
    router = APIRouter()

    @router.post(APPLICATIONS_PATH)
    async def receive(request: Request) -> JSONResponse:
        try:
            raw = json.loads(await request.body())
        except ValueError:
            return JSONResponse({"error": "invalid json"}, status_code=422)
        if not isinstance(raw, dict):
            return JSONResponse({"error": "expected a JSON object"}, status_code=422)
        try:
            result = starter.start_application(raw)
        except CandidateBusy:
            # Spec 015: otro proceso tiene ese teléfono; el portal reintenta.
            return JSONResponse(
                {"error": "candidate busy, retry later"},
                status_code=503,
                headers={"Retry-After": str(BUSY_RETRY_AFTER_SECONDS)},
            )
        return JSONResponse(
            result.model_dump(mode="json"), status_code=_STATUS_CODES[result.outcome]
        )

    return router
