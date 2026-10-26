"""Agenda `next_touch_at` en las fichas abiertas anteriores a la spec 017 (una sola vez).

Usa el mismo repositorio y la misma configuración que la API:

    SCREENING_REPOSITORY=firestore python scripts/backfill_next_touch.py

Es idempotente: solo guarda las fichas cuyo próximo toque cambia.
"""

from __future__ import annotations

import argparse

from dotenv import load_dotenv

from screening.api.wiring import build_storage
from screening.config import load_config
from screening.reengagement import backfill_next_touch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--page-size", type=int, default=200, help="fichas por consulta")
    args = parser.parse_args(argv)

    load_dotenv()
    repository, _ = build_storage()
    report = backfill_next_touch(repository, load_config(), page_size=args.page_size)
    print(f"Fichas abiertas revisadas: {report.read}; agendadas o corregidas: {report.updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
