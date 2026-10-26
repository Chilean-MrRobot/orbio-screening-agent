"""API del panel de recruiters (spec 013 y 018), protegida con `PANEL_PASSWORD`.

El panel de Streamlit lee por aquí: el repositorio en memoria vive en este proceso. Ninguna
ruta recorre todas las fichas: el listado pagina con `query` y los totales salen de
`count_by_status()`.
"""

from __future__ import annotations

import hmac
import logging
import os
from collections.abc import Mapping
from datetime import timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from screening.adapters.query import InvalidCursorError
from screening.clock import Clock
from screening.crm import (
    CandidateRowPage,
    CrmMetrics,
    InterviewChangeRequest,
    SlotOption,
    SlotUnavailableError,
    StatusChangeRequest,
    StatusCounts,
    TransitionError,
    apply_interview_change,
    apply_status_change,
    compute_metrics,
    slot_options,
    to_row,
)
from screening.domain import Candidate, CandidateStatus, Country, Stage
from screening.ports import CandidateQuery, CandidateRepository
from screening.scheduling import InterviewScheduler

logger = logging.getLogger(__name__)

CRM_PREFIX = "/crm"
PASSWORD_ENV = "PANEL_PASSWORD"
METRICS_WINDOW_ENV = "PANEL_METRICS_WINDOW"
DEFAULT_STALE_HOURS = 24.0
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
# Fichas recientes sobre las que se calcula la analítica (los totales cuentan todas).
DEFAULT_METRICS_WINDOW = 1000
QUERY_MAX_LIMIT = 500  # tope de `CandidateQuery.limit`
DEFAULT_SLOTS = 20  # horarios libres que se muestran para agendar (spec 021)

PanelOrder = Literal["score_desc", "updated_desc"]


def build_crm_router(
    repository: CandidateRepository,
    clock: Clock,
    password: str,
    *,
    metrics_window: int = DEFAULT_METRICS_WINDOW,
    scheduler: InterviewScheduler | None = None,
) -> APIRouter:
    """Con `scheduler` se suman los horarios libres y agendar o cancelar (spec 021)."""

    def require_password(authorization: Annotated[str | None, Header()] = None) -> None:
        scheme, _, value = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(value.encode(), password.encode()):
            raise HTTPException(status_code=401, detail="invalid password")

    router = APIRouter(prefix=CRM_PREFIX, tags=["panel"], dependencies=[Depends(require_password)])

    def load(candidate_id: str) -> Candidate:
        candidate = repository.get(candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="candidate not found")
        return candidate

    def recent_sample() -> list[Candidate]:
        sample: list[Candidate] = []
        cursor: str | None = None
        while len(sample) < metrics_window:
            limit = min(QUERY_MAX_LIMIT, metrics_window - len(sample))
            page = repository.query(
                CandidateQuery(order="updated_desc", limit=limit, cursor=cursor)
            )
            sample.extend(page.items)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        return sample

    @router.get("/candidates")
    def list_candidates(
        status: Annotated[list[CandidateStatus] | None, Query()] = None,
        country: Country | None = None,
        stage: Stage | None = None,
        order: PanelOrder = "score_desc",
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> CandidateRowPage:
        query = CandidateQuery(
            statuses=status, country=country, stage=stage, order=order, limit=limit, cursor=cursor
        )
        try:
            page = repository.query(query)
        except InvalidCursorError as e:
            raise HTTPException(status_code=400, detail=f"invalid cursor: {e}") from e
        return CandidateRowPage(items=[to_row(c) for c in page.items], next_cursor=page.next_cursor)

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

    if scheduler is not None:

        @router.get("/candidates/{candidate_id}/slots")
        def free_slots(
            candidate_id: str,
            limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_SLOTS,
        ) -> list[SlotOption]:
            return slot_options(load(candidate_id), scheduler, limit)

        @router.post("/candidates/{candidate_id}/interview")
        def change_interview(candidate_id: str, request: InterviewChangeRequest) -> dict[str, Any]:
            candidate = load(candidate_id)
            try:
                apply_interview_change(candidate, request, scheduler, clock.now())
            except (TransitionError, SlotUnavailableError) as e:
                raise HTTPException(status_code=409, detail=str(e)) from e
            repository.save(candidate)
            logger.info("Recruiter cambió la entrevista de %s (%s)", candidate.id, request.action)
            return candidate.model_dump(mode="json")

    @router.get("/counts")
    def counts() -> StatusCounts:
        by_status = repository.count_by_status()
        return StatusCounts(total=sum(by_status.values()), by_status=by_status)

    @router.get("/metrics")
    def metrics(
        stale_hours: Annotated[float, Query(gt=0)] = DEFAULT_STALE_HOURS,
    ) -> CrmMetrics:
        return compute_metrics(
            recent_sample(),
            clock.now(),
            timedelta(hours=stale_hours),
            status_counts=repository.count_by_status(),
            sample_limit=metrics_window,
        )

    return router


def build_crm_router_from_env(
    repository: CandidateRepository,
    clock: Clock,
    env: Mapping[str, str] = os.environ,
    *,
    scheduler: InterviewScheduler | None = None,
) -> APIRouter | None:
    """Sin `PANEL_PASSWORD` no se expone el panel: la ficha tiene datos personales."""
    password = env.get(PASSWORD_ENV)
    if not password:
        logger.warning("Sin %s: la API del panel no se monta", PASSWORD_ENV)
        return None
    window = int(env.get(METRICS_WINDOW_ENV) or DEFAULT_METRICS_WINDOW)
    if window < 1:
        raise ValueError(f"{METRICS_WINDOW_ENV} debe ser mayor que 0")
    return build_crm_router(repository, clock, password, metrics_window=window, scheduler=scheduler)
