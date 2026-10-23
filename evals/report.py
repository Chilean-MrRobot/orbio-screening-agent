"""Reporte de una corrida en JSON (para máquinas) y Markdown (para personas)."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

from evals.models import SuiteReport, UsageTotals

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def write_report(report: SuiteReport, results_dir: Path | str | None = None) -> tuple[Path, Path]:
    directory = Path(results_dir or RESULTS_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{report.suite}-{report.started_at.astimezone(UTC):%Y%m%dT%H%M%SZ}"
    json_path = directory / f"{stem}.json"
    md_path = directory / f"{stem}.md"
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def render_markdown(report: SuiteReport) -> str:
    m = report.metrics
    lines = [
        f"# Evaluación: {report.suite}",
        "",
        f"- Corrida: {report.started_at:%Y-%m-%d %H:%M:%S %Z}",
        f"- Juez: `{report.judge_model}` · prompt `{report.judge_prompt_version}`",
        "",
        "## Resumen",
        "",
        "| Métrica | Valor |",
        "|---|---|",
        f"| Accuracy | {m.accuracy:.1%} ({m.correct}/{m.total}) |",
        f"| Pass rate | {m.pass_rate:.1%} ({m.passed}/{m.total}) |",
        f"| Errores | {m.errors} |",
        f"| Latencia p50 | {_ms(m.latency_p50_ms)} |",
        f"| Latencia p95 | {_ms(m.latency_p95_ms)} |",
        f"| Objetivo | {_usage(m.target)} |",
        f"| Juez | {_usage(m.judge)} |",
        f"| Costo total | ${m.total_cost_usd:.4f} |",
        "",
        "## Casos incorrectos",
        "",
    ]
    incorrect = [c for c in report.cases if not c.correct]
    if not incorrect:
        lines.append("Ninguno.")
    for case in incorrect:
        lines += [
            f"### `{case.case_id}`",
            "",
            f"- Esperado: {case.expected_verdict} · Veredicto: {case.verdict}"
            + (f" (puntaje {case.score})" if case.score is not None else ""),
            f"- Respuesta esperada: {_inline(case.expected)}",
            f"- Respuesta del sistema: {_inline(case.answer or '—')}",
        ]
        if case.reasoning:
            lines.append(f"- Razonamiento del juez: {_inline(case.reasoning)}")
        if case.error:
            lines.append(f"- Error: {_inline(case.error)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _ms(value: float | None) -> str:
    return "—" if value is None else f"{value:.0f} ms"


def _usage(totals: UsageTotals) -> str:
    return (
        f"{totals.calls} llamadas · {totals.input_tokens} tokens de entrada · "
        f"{totals.output_tokens} de salida · ${totals.cost_usd:.4f}"
    )


def _inline(text: str) -> str:
    return " ".join(text.split())
