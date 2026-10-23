"""Trazas opcionales en LangSmith para ADK (`LANGSMITH_TRACING=true`)."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def tracing_from_env() -> bool:
    return os.environ.get("LANGSMITH_TRACING", "").strip().lower() == "true"


def configure_tracing() -> bool:
    """Activa la integración de LangSmith con ADK. Si falla, se sigue sin trazas."""
    try:
        # Solo se importa si las trazas están activas.
        from langsmith.integrations.google_adk import configure_google_adk

        enabled = configure_google_adk(project_name=os.environ.get("LANGSMITH_PROJECT") or None)
    except Exception as e:
        logger.warning("no se pudo activar LangSmith para ADK, se sigue sin trazas: %s", e)
        return False
    if not enabled:
        logger.warning("LangSmith no pudo instrumentar ADK, se sigue sin trazas")
    return bool(enabled)
