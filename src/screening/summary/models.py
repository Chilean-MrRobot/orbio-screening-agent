"""Esquemas del resumen post-conversación (spec 012)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from screening.domain import CandidateStatus, Country

NOT_INFORMED = "no informado"
NO_OBSERVATIONS = "Observaciones: no disponibles (no se pudo generar el resumen automático)."

STATUS_LABELS = {
    CandidateStatus.QUALIFIED: "Calificado",
    CandidateStatus.DISQUALIFIED: "Descartado",
}


class KeyFacts(BaseModel):
    """Datos clave armados desde la ficha: el LLM nunca los escribe."""

    full_name: str | None = None
    city: str | None = None
    country: Country | None = None
    license: str | None = None
    vehicle: str | None = None
    availability: str | None = None
    preferred_schedule: str | None = None
    delivery_experience: str | None = None
    start_date: date | None = None
    work_permit: bool | None = None
    status: CandidateStatus
    score: float | None = None
    max_score: float
    disqualification_reason: str | None = None
    needs_review: list[str] = Field(default_factory=list)
    human_requested: bool = False


class SummaryNotes(BaseModel):
    """Salida estructurada del LLM: solo lo cualitativo."""

    observations: list[str] = Field(
        min_length=1,
        max_length=3,
        description="2 a 3 frases cortas para el recruiter, basadas solo en la conversación.",
    )


class CandidateSummary(BaseModel):
    key_facts: KeyFacts
    observations: list[str] = Field(default_factory=list)
    source: Literal["llm", "fallback"]
    prompt_version: str
    error: str | None = None

    def render(self) -> str:
        """Texto que se guarda en `candidate.summary`."""
        f = self.key_facts
        place = f.city or "ciudad no informada"
        if f.country:
            place = f"{place} ({f.country})"
        header = [f.full_name or "Sin nombre", place, STATUS_LABELS.get(f.status, str(f.status))]
        if f.disqualification_reason:
            header.append(f"Motivo: {f.disqualification_reason}")
        elif f.score is not None:
            header.append(f"Puntaje {f.score:g}/{f.max_score:g}")

        lines = [
            " · ".join(header),
            f"Licencia: {_show(f.license)} · Vehículo: {_show(f.vehicle)}",
            f"Disponibilidad: {_show(f.availability)} · Horario: {_show(f.preferred_schedule)}",
            f"Experiencia: {_show(f.delivery_experience)}"
            f" · Inicio: {_show(f.start_date.isoformat() if f.start_date else None)}"
            f" · Permiso de trabajo: {_yes_no(f.work_permit)}",
        ]
        if f.needs_review:
            lines.append(f"Revisar: {', '.join(f.needs_review)}")
        if f.human_requested:
            lines.append("Pidió hablar con una persona.")
        lines.append("")
        if self.observations:
            lines.append("Observaciones:")
            lines.extend(f"- {o}" for o in self.observations)
        else:
            lines.append(NO_OBSERVATIONS)
        return "\n".join(lines)


def _show(value: str | None) -> str:
    return value or NOT_INFORMED


def _yes_no(value: bool | None) -> str:
    return NOT_INFORMED if value is None else ("sí" if value else "no")
