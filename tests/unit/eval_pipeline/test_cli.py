from __future__ import annotations

from pathlib import Path

import pytest

from evals import run as cli
from evals.models import TargetOutput
from evals.suites import SuiteSpec
from screening.adapters.fake_llm import FakeLLMClient
from tests.unit.eval_pipeline.helpers import verdict, write_jsonl

ROWS = [
    {"id": "a", "input": {"question": "q", "answer": "Sí"}, "expected": "Sí"},
    {"id": "b", "input": {"question": "q", "answer": "No"}, "expected": "Sí"},
]


def echo(llm, config):
    return lambda case: TargetOutput(answer=case.input["answer"])


@pytest.fixture
def datasets_dir(tmp_path: Path) -> Path:
    path = tmp_path / "datasets"
    path.mkdir()
    write_jsonl(path / "demo.jsonl", ROWS)
    return path


def main(datasets_dir: Path, tmp_path: Path, llm, app_config, threshold: float, *extra: str):
    return cli.main(
        ["--suite", "demo", "--datasets-dir", str(datasets_dir), "--results-dir",
         str(tmp_path / "results"), *extra],
        llm=llm,
        config=app_config,
        suites={"demo": SuiteSpec("demo", echo, threshold)},
    )  # fmt: skip


def test_cli_writes_report_and_passes_threshold(
    datasets_dir, tmp_path, judge_llm, app_config, capsys
) -> None:
    code = main(datasets_dir, tmp_path, judge_llm, app_config, 0.5)

    assert code == 0
    assert len(list((tmp_path / "results").glob("demo-*.json"))) == 1
    assert len(list((tmp_path / "results").glob("demo-*.md"))) == 1
    out = capsys.readouterr().out
    assert "Accuracy" in out and "50.0%" in out
    assert judge_llm.calls[0].model == app_config.llm.judge_model


def test_cli_fails_below_threshold(datasets_dir, tmp_path, judge_llm, app_config) -> None:
    assert main(datasets_dir, tmp_path, judge_llm, app_config, 0.9) == 1


def test_cli_limit(datasets_dir, tmp_path, app_config) -> None:
    llm = FakeLLMClient([verdict("pass")])

    code = main(datasets_dir, tmp_path, llm, app_config, 1.0, "--limit", "1")

    assert code == 0
    assert len(llm.calls) == 1


def test_cli_rejects_unknown_suite(app_config) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--suite", "nope"], llm=FakeLLMClient(), config=app_config)
