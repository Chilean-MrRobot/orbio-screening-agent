"""Llena la API local con los 12 candidatos de demo, pasando por el flujo real (spec 013).

Requiere la API de demo corriendo (agente guionado, sin LLM y sin WhatsApp real):

    uvicorn screening.crm.demo:create_demo_app --factory --port 8000
    python scripts/seed_demo.py

Por cada escenario: `POST /applications`, cada mensaje del candidato al webhook de
WhatsApp (firmado con `WHATSAPP_APP_SECRET`, como lo haría Meta) y las acciones del
recruiter por la API del CRM. Espera la respuesta del agente antes del siguiente mensaje.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
import time
from itertools import count
from typing import Any

import httpx

from screening.crm.client import CrmApiError, CrmClient
from screening.crm.demo import DEMO_SCENARIOS, DemoScenario, demo_env
from screening.domain import Candidate, MessageRole

_ids = count(1)


def main(argv: list[str] | None = None) -> int:
    env = demo_env()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api-url", default=env.get("PANEL_API_URL", "http://localhost:8000"))
    parser.add_argument("--timeout", type=float, default=30.0, help="segundos por mensaje")
    args = parser.parse_args(argv)

    api = httpx.Client(base_url=args.api_url, timeout=10)
    crm = CrmClient(args.api_url, env["PANEL_PASSWORD"])
    secret = env["WHATSAPP_APP_SECRET"]
    try:
        api.get("/healthz").raise_for_status()
    except httpx.HTTPError as e:
        print(f"La API no responde en {args.api_url}: {e}", file=sys.stderr)
        return 1

    run_id = int(time.time())
    for scenario in DEMO_SCENARIOS:
        try:
            status = seed_scenario(api, crm, secret, scenario, run_id, args.timeout)
        except (httpx.HTTPError, CrmApiError, TimeoutError) as e:
            print(f"✗ {scenario.key}: {e}", file=sys.stderr)
            continue
        print(f"✓ {scenario.key:<26} → {status}")
    return 0


def seed_scenario(
    api: httpx.Client,
    crm: CrmClient,
    secret: str,
    scenario: DemoScenario,
    run_id: int,
    timeout: float,
) -> str:
    response = api.post("/applications", json=scenario.application)
    body = response.json()
    candidate_id = body.get("candidate_id")
    if candidate_id is None:
        raise httpx.HTTPError(f"/applications respondió {response.status_code}: {body}")
    if body.get("outcome") == "duplicate":
        return f"ya existía ({body.get('status')})"

    phone = scenario.application["phone"]
    for sent, turn in enumerate(scenario.turns, start=1):
        payload = whatsapp_payload(phone, scenario.application["full_name"], turn.text, run_id)
        raw = json.dumps(payload).encode()
        signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        api.post(
            "/webhooks/whatsapp",
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": f"sha256={signature}",
            },
        ).raise_for_status()
        wait_for_reply(crm, candidate_id, sent, timeout)

    for step in scenario.recruiter:
        crm.change_status(candidate_id, step.status, reason=step.reason, recruiter=step.recruiter)
    return crm.get_candidate(candidate_id).status.value


def whatsapp_payload(phone: str, name: str, text: str, run_id: int) -> dict[str, Any]:
    """Webhook de Meta con un mensaje de texto (solo los campos que lee la API)."""
    wa_id = phone.lstrip("+")
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "contacts": [{"wa_id": wa_id, "profile": {"name": name}}],
                            "messages": [
                                {
                                    "from": wa_id,
                                    "id": f"wamid.demo-{run_id}-{next(_ids)}",
                                    "timestamp": str(int(time.time())),
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ]
            }
        ],
    }


def wait_for_reply(crm: CrmClient, candidate_id: str, sent: int, timeout: float) -> Candidate:
    """Hasta que la ficha tenga `sent` mensajes del candidato y el último sea del agente."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        candidate = crm.get_candidate(candidate_id)
        received = sum(m.role == MessageRole.CANDIDATE for m in candidate.messages)
        if received >= sent and candidate.messages[-1].role == MessageRole.AGENT:
            return candidate
        time.sleep(0.3)
    raise TimeoutError(f"sin respuesta del agente tras {timeout:g} s")


if __name__ == "__main__":
    raise SystemExit(main())
