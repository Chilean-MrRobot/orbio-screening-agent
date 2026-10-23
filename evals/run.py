"""CLI: `python -m evals.run --suite <nombre>`. Sale con 1 si la accuracy queda bajo el umbral."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence

from evals.dataset import load_suite
from evals.judge import Judge
from evals.models import CaseResult
from evals.report import write_report
from evals.runner import run_suite
from evals.suites import SUITES, SuiteSpec
from screening.clock import Clock, SystemClock
from screening.config import AppConfig, load_config
from screening.ports import LLMClient


def main(
    argv: Sequence[str] | None = None,
    *,
    llm: LLMClient | None = None,
    config: AppConfig | None = None,
    clock: Clock | None = None,
    suites: Mapping[str, SuiteSpec] = SUITES,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.run", description=__doc__)
    parser.add_argument("--suite", required=True, choices=sorted(suites))
    parser.add_argument("--limit", type=int, help="corre solo los primeros N casos")
    parser.add_argument("--datasets-dir", help="carpeta de los JSONL (por defecto evals/datasets)")
    parser.add_argument("--results-dir", help="carpeta del reporte (por defecto evals/results)")
    args = parser.parse_args(argv)

    config = config or load_config()
    if llm is None:
        from screening.adapters.openai_llm import OpenAILLMClient

        llm = OpenAILLMClient.from_env()
    spec = suites[args.suite]
    cases = load_suite(args.suite, args.datasets_dir)[: args.limit]

    report = run_suite(
        args.suite,
        cases,
        spec.make_target(llm, config),
        Judge(llm, config.llm.judge_model),
        clock=clock or SystemClock(),
        on_result=_print_case,
    )
    json_path, md_path = write_report(report, args.results_dir)

    m = report.metrics
    print(
        f"\nAccuracy {m.accuracy:.1%} ({m.correct}/{m.total}, umbral {spec.threshold:.0%}) · "
        f"p50 {m.latency_p50_ms or 0:.0f} ms · p95 {m.latency_p95_ms or 0:.0f} ms · "
        f"costo ${m.total_cost_usd:.4f} (objetivo ${m.target.cost_usd:.4f}, "
        f"juez ${m.judge.cost_usd:.4f})"
    )
    print(f"Reporte: {md_path}\n         {json_path}")
    return 0 if m.accuracy >= spec.threshold else 1


def _print_case(result: CaseResult) -> None:
    mark = "OK  " if result.correct else "MAL "
    detail = result.error or f"{result.verdict} (esperado {result.expected_verdict})"
    print(f"{mark}{result.case_id}: {detail}")


if __name__ == "__main__":
    sys.exit(main())
