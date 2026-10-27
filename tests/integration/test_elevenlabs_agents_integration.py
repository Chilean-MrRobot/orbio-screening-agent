"""Token de conversación real de ElevenLabs Agents (spec 022).

Se salta sin `ELEVENLABS_API_KEY` y `ELEVENLABS_AGENT_ID` (el agente se crea con
`deploy/elevenlabs_agent.py`).
"""

from __future__ import annotations

import os

import pytest

from screening.adapters.elevenlabs_agents import ElevenLabsAgents

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (os.environ.get("ELEVENLABS_API_KEY") and os.environ.get("ELEVENLABS_AGENT_ID")),
        reason="faltan ELEVENLABS_API_KEY o ELEVENLABS_AGENT_ID",
    ),
]


def test_conversation_token_for_the_agent() -> None:
    agents = ElevenLabsAgents(os.environ["ELEVENLABS_API_KEY"])

    token = agents.conversation_token(os.environ["ELEVENLABS_AGENT_ID"])

    assert isinstance(token, str) and len(token) > 20
