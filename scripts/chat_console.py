"""Conversación real por consola: el mismo orquestador y agente que en WhatsApp, sin Meta.

Uso:
    python scripts/chat_console.py          # interactivo (escribe "salir" para terminar)
    python scripts/chat_console.py --country MX --messages "sí" "tengo licencia A, carro, CDMX"

Necesita `OPENAI_API_KEY` (se lee de `.env`). Al final imprime la ficha, el puntaje, el
resumen y el costo en tokens de la conversación.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import timedelta
from itertools import count

from dotenv import load_dotenv

from screening.adapters.memory import InMemoryCandidateRepository
from screening.api.wiring import UnconfiguredAgent, load_agent, load_summarizer
from screening.channels import ConsoleChannel, InboundBatch, InboundMessage
from screening.clock import SystemClock
from screening.config import load_config
from screening.conversation import ConversationOrchestrator
from screening.conversation.usage import usage_totals

PHONES = {"ES": "+34600000001", "MX": "+525500000001"}


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--name", default="Lucía Pérez")
    parser.add_argument("--country", choices=sorted(PHONES), default="ES")
    parser.add_argument("--messages", nargs="*", help="mensajes del candidato, sin interacción")
    args = parser.parse_args()

    config = load_config()
    clock = SystemClock()
    agent = load_agent(config, clock)
    if isinstance(agent, UnconfiguredAgent):
        print("Falta OPENAI_API_KEY en .env", file=sys.stderr)
        return 1

    repository = InMemoryCandidateRepository()
    orchestrator = ConversationOrchestrator(
        repository=repository,
        agent=agent,
        channel=ConsoleChannel(),
        clock=clock,
        config=config,
        summarizer=load_summarizer(config),
    )
    phone = PHONES[args.country]
    orchestrator.start_application(
        {
            "application_id": "console-1",
            "source": "console",
            "full_name": args.name,
            "phone": phone,
            "country": args.country,
        }
    )

    ids = count(1)
    for text in _messages(args.messages):
        print(f"[candidato] {text}", flush=True)
        now = clock.now()
        message = InboundMessage(
            external_id=f"console-in-{next(ids)}",
            sender=phone,
            text=text,
            type="text",
            profile_name=args.name,
            sent_at=now - timedelta(seconds=1),
            received_at=now,
        )
        start = time.perf_counter()
        orchestrator.handle(InboundBatch(sender=phone, messages=[message]))
        print(f"   ({time.perf_counter() - start:.1f} s)", flush=True)
        candidate = repository.get_by_phone(phone)
        if candidate is not None and candidate.stage == "done":
            break

    candidate = repository.get_by_phone(phone)
    if candidate is None:
        return 1
    totals = usage_totals(candidate)
    print("\n=== Ficha ===")
    print(f"estado={candidate.status} etapa={candidate.stage} idioma={candidate.language}")
    print(json.dumps(candidate.data.model_dump(mode="json", exclude_none=True), ensure_ascii=False))
    if candidate.knockout:
        print(f"eliminatorias: {candidate.knockout.model_dump(mode='json')}")
    if candidate.score:
        print(f"puntaje: {candidate.score.total} {candidate.score.components}")
    if candidate.summary:
        print(f"\n=== Resumen ===\n{candidate.summary}")
    print(
        f"\n=== Costo === {totals.calls} llamadas · {totals.input_tokens}+{totals.output_tokens}"
        f" tokens · ${totals.cost_usd:.4f}"
    )
    return 0


def _messages(scripted: list[str] | None):
    if scripted is not None:
        yield from scripted
        return
    while True:
        try:
            text = input("> ").strip()
        except EOFError:
            return
        if text.lower() in {"salir", "exit", "quit"}:
            return
        if text:
            yield text


if __name__ == "__main__":
    raise SystemExit(main())
