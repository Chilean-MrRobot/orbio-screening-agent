"""API del CRM para recruiters (spec 013), protegida con `CRM_API_TOKEN`.

El CRM de Streamlit lee por aquí: el repositorio en memoria vive en este proceso.
"""

from __future__ import annotations

import hmac
import logging
import os
from collections.abc import Mapping
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from screening.clock import Clock
from screening.crm import (
    CandidateRow,
    CrmMetrics,
    StatusChangeRequest,
    TransitionError,
    apply_status_change,
    compute_metrics,
    filter_and_sort,
    to_row,
)
from screening.domain import Candidate, CandidateStatus, Country, Stage
from screening.ports import CandidateRepository

logger = logging.getLogger(__name__)

CRM_PREFIX = "/crm"
TOKEN_ENV = "CRM_API_TOKEN"
DEFAULT_STALE_HOURS = 24.0


def build_crm_router(repository: CandidateRepository, clock: Clock, token: str) -> APIRouter:
    def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
        scheme, _, value = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(value.encode(), token.encode()):
            raise HTTPException(status_code=401, detail="invalid token")

    router = APIRouter(prefix=CRM_PREFIX, tags=["crm"], dependencies=[Depends(require_token)])

    def load(candidate_id: str) -> Candidate:
        candidate = repository.get(candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="candidate not found")
        return candidate

    @router.get("/candidates")
    def list_candidates(
        status: Annotated[list[CandidateStatus] | None, Query()] = None,
        country: Country | None = None,
        stage: Stage | None = None,
    ) -> list[CandidateRow]:
        selected = filter_and_sort(repository.list(), statuses=status, country=country, stage=stage)
        return [to_row(c) for c in selected]

    @router.get("/candidates/{candidate_id}")
    def get_candidate(candidate_id: str) -> dict[str, Any]:
        return load(candidate_id).model_dump(mode="json")

    @router.post("/candidates/{candidate_id}/status")
    def change_status(candidate_id: str, request: StatusChangeRequest) -> dict[str, Any]:
        candidate = load(candidate_id)
        try:
            apply_status_change(candidate, request, clock.now())
        except TransitionError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        repository.save(candidate)
        logger.info("Recruiter movió %s a %s", candidate.id, candidate.status)
        return candidate.model_dump(mode="json")

    @router.get("/metrics")
    def metrics(
        stale_hours: Annotated[float, Query(gt=0)] = DEFAULT_STALE_HOURS,
    ) -> CrmMetrics:
        return compute_metrics(repository.list(), clock.now(), timedelta(hours=stale_hours))

    return router


def build_crm_router_from_env(
    repository: CandidateRepository, clock: Clock, env: Mapping[str, str] = os.environ
) -> APIRouter | None:
    """Sin `CRM_API_TOKEN` no se expone el CRM: la ficha tiene datos personales."""
    token = env.get(TOKEN_ENV)
    if not token:
        logger.warning("Sin %s: la API del CRM no se monta", TOKEN_ENV)
        return None
    return build_crm_router(repository, clock, token)
