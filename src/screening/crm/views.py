"""Lógica de datos de la UI del CRM: etiquetas, tablas y formato (sin Streamlit)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from screening.crm.listing import CandidateRow
from screening.crm.metrics import CrmMetrics
from screening.domain import (
    Candidate,
    CandidateStatus,
    InterviewModality,
    InterviewSlot,
    Language,
    MessageRole,
    Stage,
    TimelineEvent,
)
from screening.scheduling import format_slot

STATUS_LABELS: dict[CandidateStatus, str] = {
    CandidateStatus.NEW: "Nuevo",
    CandidateStatus.CONTACTED: "Contactado",
    CandidateStatus.IN_SCREENING: "En screening",
    CandidateStatus.QUALIFIED: "Calificado",
    CandidateStatus.DISQUALIFIED: "Descartado",
    CandidateStatus.NO_RESPONSE: "Sin respuesta",
    CandidateStatus.INVALID_DATA: "Datos inválidos",
    CandidateStatus.OPTED_OUT: "Pidió no ser contactado",
    CandidateStatus.INTERVIEW: "Entrevista",
    CandidateStatus.HIRED: "Contratado",
}

STATUS_ICONS: dict[CandidateStatus, str] = {
    CandidateStatus.NEW: "⚪",
    CandidateStatus.CONTACTED: "🔵",
    CandidateStatus.IN_SCREENING: "🟡",
    CandidateStatus.QUALIFIED: "🟢",
    CandidateStatus.DISQUALIFIED: "🔴",
    CandidateStatus.NO_RESPONSE: "⚫",
    CandidateStatus.INVALID_DATA: "🟠",
    CandidateStatus.OPTED_OUT: "⚫",
    CandidateStatus.INTERVIEW: "🟣",
    CandidateStatus.HIRED: "⭐",
}

STATUS_COLORS: dict[CandidateStatus, str] = {
    CandidateStatus.NEW: "gray",
    CandidateStatus.CONTACTED: "blue",
    CandidateStatus.IN_SCREENING: "orange",
    CandidateStatus.QUALIFIED: "green",
    CandidateStatus.DISQUALIFIED: "red",
    CandidateStatus.NO_RESPONSE: "gray",
    CandidateStatus.INVALID_DATA: "orange",
    CandidateStatus.OPTED_OUT: "gray",
    CandidateStatus.INTERVIEW: "violet",
    CandidateStatus.HIRED: "green",
}

STAGE_LABELS: dict[Stage, str] = {
    Stage.WELCOME: "Bienvenida",
    Stage.KNOCKOUT: "Requisitos",
    Stage.PROFILE: "Perfil",
    Stage.CLOSING: "Cierre",
    Stage.SCHEDULING: "Agendamiento",
    Stage.DONE: "Terminada",
}

MODALITY_LABELS: dict[InterviewModality, str] = {
    InterviewModality.VIDEO: "Videollamada",
    InterviewModality.IN_PERSON: "Presencial",
}

# Por qué la entrevista quedó para agendar a mano (spec 021).
MANUAL_REASON_LABELS: dict[str, str] = {
    "declined_options": "Ninguno de los horarios ofrecidos le sirvió",
    "no_slots": "No había horarios libres",
    "no_response": "No respondió a la oferta de horarios",
    "human_requested": "Pidió hablar con una persona",
    "cancelled": "El recruiter canceló la entrevista",
}

SHORT_WEEKDAYS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")

ACTION_LABELS: dict[CandidateStatus, str] = {
    CandidateStatus.INTERVIEW: "Pasar a entrevista",
    CandidateStatus.HIRED: "Marcar contratado",
    CandidateStatus.DISQUALIFIED: "Descartar",
}

REASON_LABELS: dict[str, str] = {
    "no_consent": "No aceptó el tratamiento de datos",
    "no_license": "Sin licencia vigente",
    "license_vehicle_mismatch": "Licencia no habilita el vehículo",
    "out_of_service_area": "Fuera de zona de servicio",
    "manual": "Descarte del recruiter",
}

FIELD_LABELS: dict[str, str] = {
    "full_name": "Nombre",
    "consent": "Consentimiento",
    "has_license": "Tiene licencia",
    "license_type": "Tipo de licencia",
    "license_years": "Años de licencia",
    "vehicle_type": "Vehículo",
    "owns_vehicle": "Vehículo propio",
    "city": "Ciudad",
    "availability": "Disponibilidad",
    "preferred_schedule": "Horario preferido",
    "delivery_experience": "Experiencia en reparto",
    "start_date": "Fecha de inicio",
    "work_permit": "Permiso de trabajo",
}

EVENT_LABELS: dict[str, str] = {
    "candidate_created": "Ficha creada",
    "status_changed": "Cambio de estado",
    "stage_changed": "Cambio de etapa",
    "message_received": "Mensaje recibido",
    "message_sent": "Mensaje enviado",
    "message_failed": "Envío fallido",
    "field_updated": "Dato extraído",
    "field_invalid": "Respuesta inválida",
    "field_needs_review": "Dato a revisar",
    "knockouts_evaluated": "Reglas evaluadas",
    "knockouts_skipped": "Reglas omitidas",
    "score_computed": "Puntaje calculado",
    "human_requested": "Pidió hablar con una persona",
    "question_received": "Pregunta del candidato",
    "language_changed": "Cambio de idioma",
    "llm_usage": "Llamada al LLM",
    "agent_error": "Error del agente",
    "orchestrator_error": "Error del orquestador",
    "message_after_close": "Mensaje tras el cierre",
    "recruiter_action": "Acción del recruiter",
    "interview_offered": "Horarios ofrecidos",
    "interview_booked": "Entrevista agendada",
    "interview_rescheduled": "Entrevista reagendada",
    "interview_cancelled": "Entrevista cancelada",
    "interview_slot_taken": "Horario ya ocupado",
    "interview_declined": "Rechazó los horarios",
    "interview_manual_required": "Agendar a mano",
}

ROLE_AVATARS: dict[MessageRole, str] = {
    MessageRole.CANDIDATE: "user",
    MessageRole.AGENT: "assistant",
    MessageRole.SYSTEM: "assistant",
}


def status_label(status: CandidateStatus) -> str:
    return f"{STATUS_ICONS[status]} {STATUS_LABELS[status]}"


def reason_label(reason: str | None) -> str:
    if reason is None:
        return ""
    return REASON_LABELS.get(reason, reason)


def table_records(rows: list[CandidateRow]) -> list[dict[str, Any]]:
    """Registros para `st.dataframe`, en el orden que entrega la API."""
    return [
        {
            "Nombre": row.name or "—",
            "Estado": status_label(row.status),
            "Etapa": STAGE_LABELS[row.stage],
            "Puntaje": row.score,
            "Ciudad": row.city or "—",
            "País": row.country.value if row.country else "—",
            "Último mensaje": _last_message(row),
            "Humano": "🙋 Sí" if row.human_requested else "",
            "Motivo": reason_label(row.disqualification_reason),
            "Entrevista": interview_cell(row),
            "Actualizado": format_time(row.updated_at),
        }
        for row in rows
    ]


def interview_cell(row: CandidateRow) -> str:
    """`mar 27/10 10:00 · Videollamada` en hora local, o el aviso de agendar a mano."""
    if row.interview_start is not None and row.interview_timezone:
        local = row.interview_start.astimezone(ZoneInfo(row.interview_timezone))
        when = f"{SHORT_WEEKDAYS[local.weekday()]} {local:%d/%m %H:%M}"
        modality = MODALITY_LABELS[row.interview_modality] if row.interview_modality else ""
        return f"{when} · {modality}" if modality else when
    return "📅 Agendar a mano" if row.scheduling_manual else ""


def interview_details(candidate: Candidate) -> list[dict[str, str]]:
    """Filas de la entrevista agendada para la ficha; vacía si no hay entrevista."""
    interview = candidate.interview
    if interview is None:
        return []
    slot = InterviewSlot(start=interview.start, end=interview.end)
    rows = [
        {"Campo": "Fecha y hora", "Valor": format_slot(slot, interview.timezone, Language.ES)},
        {"Campo": "Zona horaria", "Valor": interview.timezone},
        {"Campo": "Modalidad", "Valor": MODALITY_LABELS[interview.modality]},
    ]
    if interview.location:
        rows.append({"Campo": "Lugar", "Valor": interview.location})
    booked_by = "Sofía (agente)" if interview.booked_by == "agent" else interview.booked_by
    rows.append({"Campo": "Agendada por", "Valor": booked_by})
    return rows


def manual_scheduling_note(candidate: Candidate) -> str | None:
    state = candidate.scheduling
    if candidate.interview is not None or not state.manual_required:
        return None
    reason = MANUAL_REASON_LABELS.get(state.manual_reason or "", state.manual_reason or "")
    return f"📅 Agendar la entrevista a mano: {reason}." if reason else "📅 Agendar a mano."


def chat_messages(candidate: Candidate) -> list[dict[str, str]]:
    return [
        {
            "avatar": ROLE_AVATARS[m.role],
            "text": m.text,
            "caption": f"{format_time(m.created_at)} · {m.channel.value}",
        }
        for m in candidate.messages
    ]


def data_rows(candidate: Candidate) -> list[dict[str, str]]:
    """Los datos extraídos, con los pendientes y los que quedaron a revisar."""
    data = candidate.data.model_dump(mode="json")
    review = set(candidate.data.needs_review)
    rows = []
    for field, label in FIELD_LABELS.items():
        value = data.get(field)
        if field in review:
            shown = "⚠️ a revisar"
        elif value is None:
            shown = "—"
        else:
            shown = format_value(value)
        rows.append({"Campo": label, "Valor": shown})
    return rows


def score_rows(candidate: Candidate) -> list[dict[str, Any]]:
    if candidate.score is None:
        return []
    return [
        {"Componente": name.replace("_", " ").capitalize(), "Puntos": points}
        for name, points in sorted(candidate.score.components.items(), key=lambda kv: -kv[1])
    ]


def timeline_rows(timeline: list[TimelineEvent]) -> list[dict[str, str]]:
    """Del más reciente al más antiguo, como un registro de actividad."""
    return [
        {
            "Hora": format_time(event.at),
            "Evento": EVENT_LABELS.get(event.type, event.type),
            "Detalle": describe_event(event),
        }
        for event in reversed(timeline)
    ]


def describe_event(event: TimelineEvent) -> str:
    d = event.detail
    match event.type:
        case "status_changed":
            text = f"{_status(d.get('previous'))} → {_status(d.get('new'))}"
            return f"{text} ({reason_label(d['reason'])})" if d.get("reason") else text
        case "stage_changed":
            return f"{_stage(d.get('previous'))} → {_stage(d.get('new'))}"
        case "field_updated":
            label = FIELD_LABELS.get(d.get("field", ""), d.get("field"))
            return f"{label}: {format_value(d.get('value'))}"
        case "field_invalid" | "field_needs_review":
            return FIELD_LABELS.get(d.get("field", ""), str(d.get("field")))
        case "knockouts_evaluated":
            if d.get("failed_rule"):
                return f"Falló {d['failed_rule']} ({reason_label(d.get('reason_code'))})"
            return f"Pasaron: {', '.join(d.get('passed') or []) or '—'}"
        case "score_computed":
            return f"{d.get('total')} / 100"
        case "llm_usage":
            return (
                f"{d.get('turn')} · {d.get('model')} · "
                f"{d.get('input_tokens')}+{d.get('output_tokens')} tokens · "
                f"US$ {float(d.get('cost_usd', 0)):.5f}"
            )
        case "message_sent" | "message_failed":
            return d.get("template") or d.get("error") or "respuesta del agente"
        case "recruiter_action":
            who = d.get("recruiter") or "recruiter"
            reason = f" · {d['reason']}" if d.get("reason") else ""
            return f"{who}: {_status(d.get('status'))}{reason}"
        case "question_received":
            return str(d.get("question"))
        case "interview_booked" | "interview_rescheduled" | "interview_cancelled":
            who = d.get("booked_by") or d.get("recruiter")
            text = format_value(d.get("start") or "")
            return f"{text} · {'Sofía' if who == 'agent' else who}" if who else text
        case "interview_manual_required":
            return MANUAL_REASON_LABELS.get(d.get("reason", ""), str(d.get("reason")))
        case _:
            return ", ".join(f"{k}={v}" for k, v in d.items() if v not in (None, [], ""))


def funnel(metrics: CrmMetrics) -> dict[str, int]:
    return {
        "Postulaciones": metrics.total,
        "Contactados": metrics.contacted,
        "Respondieron": metrics.responded,
        "Completaron": metrics.completed,
        "Calificados": metrics.qualified,
    }


def status_counts(metrics: CrmMetrics) -> dict[str, int]:
    return {STATUS_LABELS[s]: n for s, n in metrics.by_status.items() if n}


def drop_off_counts(metrics: CrmMetrics) -> dict[str, int]:
    return {STAGE_LABELS[s]: n for s, n in metrics.drop_off_by_stage.items() if s != Stage.DONE}


def reason_counts(metrics: CrmMetrics) -> dict[str, int]:
    return {reason_label(r): n for r, n in metrics.disqualification_reasons.items()}


def format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, dict) and "years" in value:
        platforms = ", ".join(value.get("platforms") or [])
        years = value["years"]
        text = f"{years:g} años" if isinstance(years, int | float) else str(years)
        return f"{text} ({platforms})" if platforms else text
    if isinstance(value, list):
        return ", ".join(map(str, value))
    return str(value)


def format_time(at: datetime) -> str:
    return at.strftime("%d/%m %H:%M")


def format_minutes(minutes: float | None) -> str:
    if minutes is None:
        return "—"
    if minutes < 1:
        return f"{minutes * 60:.0f} s"
    if minutes < 60:
        return f"{minutes:.0f} min"
    if minutes < 60 * 24:
        return f"{minutes / 60:.1f} h"
    return f"{minutes / (60 * 24):.1f} días"


def format_rate(rate: float | None) -> str:
    return "—" if rate is None else f"{rate:.0%}"


def _last_message(row: CandidateRow) -> str:
    if row.last_message is None:
        return ""
    who = "👤" if row.last_message.role == MessageRole.CANDIDATE else "🤖"
    text = row.last_message.text
    return f"{who} {text[:80]}…" if len(text) > 80 else f"{who} {text}"


def _status(value: Any) -> str:
    try:
        return STATUS_LABELS[CandidateStatus(value)]
    except ValueError:
        return str(value)


def _stage(value: Any) -> str:
    try:
        return STAGE_LABELS[Stage(value)]
    except ValueError:
        return str(value)
