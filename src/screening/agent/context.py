"""Armado del contexto de cada turno desde la ficha: fecha local, historial e instrucciones."""

from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from screening.config import AppConfig
from screening.config.models import RegionConfig
from screening.domain import Candidate, Language, Message, MessageRole
from screening.ports import ReplyRequest

WEEKDAYS_ES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
LANGUAGE_NAMES = {Language.ES: "español", Language.EN: "inglés"}
NO_NEW_MESSAGES = "(El candidato no escribió nada nuevo: inicia o retoma la conversación.)"


def region_for(candidate: Candidate, config: AppConfig) -> RegionConfig:
    """Región del candidato; si no se conoce el país, la primera configurada."""
    country = candidate.country or candidate.application.country
    if country is not None and country in config.regions:
        return config.regions[country]
    return config.regions[min(config.regions)]


def local_today(now: datetime, timezone: str) -> date:
    return now.astimezone(ZoneInfo(timezone)).date()


def describe_date(day: date) -> str:
    return f"{day.isoformat()} ({WEEKDAYS_ES[day.weekday()]})"


def first_name(candidate: Candidate) -> str | None:
    name = candidate.data.full_name or candidate.application.full_name
    return name.split()[0] if name and name.strip() else None


def conversation(messages: list[Message], limit: int) -> list[Message]:
    """Mensajes del candidato y del agente, los últimos `limit` (los de sistema no se muestran)."""
    visible = [m for m in messages if m.role != MessageRole.SYSTEM and m.text.strip()]
    return visible[-limit:] if limit > 0 else []


def known_data(candidate: Candidate) -> str:
    data = candidate.data.model_dump(mode="json", exclude_none=True, exclude={"needs_review"})
    return json.dumps(data, ensure_ascii=False) if data else "(ninguno)"


def licenses_table(region: RegionConfig) -> str:
    return "; ".join(
        f"{code} ({', '.join(v.value for v in spec.vehicles)})"
        for code, spec in region.licenses.items()
    )


def build_extraction_input(
    candidate: Candidate,
    new_messages: list[Message],
    expected_fields: list[str],
    *,
    region: RegionConfig,
    today: date,
    agent_name: str,
    history_limit: int,
) -> str:
    """Mensaje de usuario del turno de extracción: contexto + mensajes nuevos, por separado."""
    new_ids = {m.id for m in new_messages}
    previous = conversation([m for m in candidate.messages if m.id not in new_ids], history_limit)
    history = "\n".join(_line(m, agent_name) for m in previous) or "(sin mensajes anteriores)"
    news = "\n".join(f"- {m.text.strip()}" for m in new_messages if m.text.strip())
    return "\n".join(
        [
            f"Fecha de hoy: {describe_date(today)}",
            f"País: {region.country.value}",
            f"Etapa: {candidate.stage.value}",
            f"Campos esperados en esta etapa: {', '.join(expected_fields) or '(ninguno)'}",
            f"Licencias válidas del país (código y vehículos): {licenses_table(region)}",
            f"Datos ya registrados: {known_data(candidate)}",
            "",
            "Conversación reciente:",
            history,
            "",
            "Mensajes nuevos del candidato:",
            news,
        ]
    )


def split_for_reply(messages: list[Message], limit: int) -> tuple[list[Message], list[Message]]:
    """(historial hasta el último mensaje del agente, mensajes del candidato posteriores)."""
    visible = conversation(messages, limit)
    last_agent = max((i for i, m in enumerate(visible) if m.role == MessageRole.AGENT), default=-1)
    return visible[: last_agent + 1], visible[last_agent + 1 :]


def build_turn_instructions(request: ReplyRequest, *, today: date) -> str:
    """Lo que decidió el orquestador para este turno, en palabras para el modelo."""
    candidate = request.candidate
    lines = [
        "## Instrucciones del turno",
        f"- Idioma del mensaje: {LANGUAGE_NAMES[candidate.language]}.",
        f"- Nombre de pila del candidato: {first_name(candidate) or '(desconocido)'}.",
        f"- Fecha de hoy: {describe_date(today)}.",
        f"- Etapa: {request.stage.value}.",
        f"- Estado de ánimo del candidato: {candidate.sentiment.value}.",
    ]
    if candidate.human_requested:
        lines.append("- El candidato pidió hablar con una persona del equipo.")
    if request.question:
        lines.append(f'- Pregunta del candidato para responder primero: "{request.question}".')
    if request.closing_summary:
        lines.append(
            "- Haz el resumen de cierre con estos datos y pide confirmación: "
            f"{known_data(candidate)}."
        )
    elif request.retry_fields:
        lines.append(
            f"- Repregunta con un ejemplo el dato `{request.retry_fields[0]}`: "
            "su respuesta anterior no sirvió o fue ambigua."
        )
    elif request.ask_fields:
        lines.append(f"- Pregunta el dato `{request.ask_fields[0]}`.")
    elif not request.question:
        lines.append("- No hay nada que preguntar en este turno.")
    return "\n".join(lines)


def _line(message: Message, agent_name: str) -> str:
    speaker = agent_name if message.role == MessageRole.AGENT else "Candidato"
    return f"{speaker}: {message.text.strip()}"
