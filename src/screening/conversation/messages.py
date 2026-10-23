"""Render de plantillas fijas (`templates.yaml`) con las variables del candidato."""

from __future__ import annotations

from screening.config import AppConfig
from screening.domain import Candidate, Language

# Textos que aún no están en `templates.yaml` (los YAML existentes no se tocan desde la
# 008; se propone moverlos en el PR). Si la clave aparece en el YAML, el YAML gana.
DEFAULT_TEMPLATES: dict[str, dict[Language, str]] = {
    "apology": {
        Language.ES: "Perdona, {first_name}, tuve un problema técnico. "
        "¿Me lo puedes repetir en un momento?",
        Language.EN: "Sorry, {first_name}, I had a technical issue. "
        "Could you repeat that in a moment?",
    },
    "human_handoff": {
        Language.ES: "Entendido, {first_name}. Le aviso al equipo de {client_name} "
        "para que una persona te contacte.",
        Language.EN: "Got it, {first_name}. I'll let the {client_name} team know "
        "so a person can contact you.",
    },
}


class _Variables(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render_template(key: str, candidate: Candidate, config: AppConfig) -> str:
    texts = config.templates.get(key) or DEFAULT_TEMPLATES[key]
    text = texts.get(candidate.language) or texts[Language.ES]
    return text.format_map(template_variables(candidate, config))


def template_variables(candidate: Candidate, config: AppConfig) -> dict[str, str]:
    region = config.regions.get(candidate.country) if candidate.country else None
    name = candidate.data.full_name or candidate.application.full_name or ""
    return _Variables(
        first_name=name.split()[0] if name.split() else "",
        agent_name=config.client.agent_name,
        client_name=config.client.name,
        sla_hours=str(config.client.recruiter_sla_hours),
        license_term=region.vocabulary.get("license", "") if region else "",
    )
