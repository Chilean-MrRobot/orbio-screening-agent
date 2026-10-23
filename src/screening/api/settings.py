"""Configuración del webhook y del canal de WhatsApp, leída del entorno."""

from __future__ import annotations

import os
from collections.abc import Mapping

from pydantic import BaseModel, Field, SecretStr

from screening.channels.whatsapp import DEFAULT_API_VERSION


class WhatsAppSettings(BaseModel):
    verify_token: SecretStr
    app_secret: SecretStr
    access_token: SecretStr | None = None
    phone_number_id: str | None = None
    batch_seconds: float = Field(default=8.0, ge=0)
    api_version: str = DEFAULT_API_VERSION

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> WhatsAppSettings:
        missing = [
            name for name in ("WHATSAPP_VERIFY_TOKEN", "WHATSAPP_APP_SECRET") if not env.get(name)
        ]
        if missing:
            raise ValueError(f"Faltan variables de entorno: {', '.join(missing)}")
        return cls(
            verify_token=SecretStr(env["WHATSAPP_VERIFY_TOKEN"]),
            app_secret=SecretStr(env["WHATSAPP_APP_SECRET"]),
            access_token=SecretStr(env["WHATSAPP_ACCESS_TOKEN"])
            if env.get("WHATSAPP_ACCESS_TOKEN")
            else None,
            phone_number_id=env.get("WHATSAPP_PHONE_NUMBER_ID") or None,
            batch_seconds=float(env.get("WHATSAPP_BATCH_SECONDS") or 8.0),
            api_version=env.get("WHATSAPP_GRAPH_VERSION") or DEFAULT_API_VERSION,
        )
