"""Crea o actualiza el agente de voz de ElevenLabs (spec 022). Idempotente; no imprime secretos.

Uso: python deploy/elevenlabs_agent.py --api-url <URL pública de la API> [--env .env]

Lee del `.env` (o del entorno) `ELEVENLABS_API_KEY`, `VOICE_LLM_SECRET` y, si existen,
`ELEVENLABS_AGENT_ID`, `ELEVENLABS_VOICE_ID` y `ELEVENLABS_AGENT_TTS_MODEL`. Sube el secreto al
workspace de ElevenLabs, crea o actualiza el agente con Custom LLM hacia
`<api-url>/voice/llm/v1` e imprime el `ELEVENLABS_AGENT_ID` que hay que guardar.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence

import httpx
from dotenv import dotenv_values

from screening.adapters.elevenlabs import ElevenLabsSettings, VoiceError
from screening.adapters.elevenlabs_agents import (
    AGENT_TTS_MODEL,
    AgentSpec,
    ElevenLabsAgents,
    sync_agent,
)
from screening.config import load_config
from screening.conversation.messages import agent_first_message
from screening.domain import Language

DEFAULT_NAME = "Sofía · Grupo Sazón (Orbio demo)"


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api-url", required=True, help="URL pública de screening-api")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--name", default=DEFAULT_NAME)
    args = parser.parse_args(argv)
    if env is None:
        env = {**{k: v for k, v in dotenv_values(args.env).items() if v}, **os.environ}

    voice = ElevenLabsSettings.from_env(env)
    llm_secret = (env.get("VOICE_LLM_SECRET") or "").strip()
    if voice is None or not llm_secret:
        print(
            "Faltan ELEVENLABS_API_KEY o VOICE_LLM_SECRET. Genera el secreto con:\n"
            '  python -c "import secrets; print(secrets.token_urlsafe(32))"',
            file=sys.stderr,
        )
        return 2

    config = load_config(env.get("SCREENING_CONFIG_DIR") or "config")
    spec = AgentSpec(
        name=args.name,
        api_url=args.api_url,
        voice_id=voice.voice_id,
        tts_model=env.get("ELEVENLABS_AGENT_TTS_MODEL") or AGENT_TTS_MODEL,
        first_messages={lang: agent_first_message(config, lang) for lang in Language},
    )
    client = ElevenLabsAgents(voice.api_key, transport=transport)
    try:
        result = sync_agent(
            client, spec, llm_secret=llm_secret, agent_id=env.get("ELEVENLABS_AGENT_ID") or None
        )
    except VoiceError as e:
        print(f"Error de ElevenLabs: {e}", file=sys.stderr)
        return 1

    action = "creado" if result.created else "actualizado"
    print(f"Agente {action}: {args.name}")
    print(f"Custom LLM: {spec.api_url.rstrip('/')}/voice/llm/v1 (secreto en el workspace)")
    print("Guarda en el .env y vuelve a correr deploy/sync_secrets.py:")
    print(f"ELEVENLABS_AGENT_ID={result.agent_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
