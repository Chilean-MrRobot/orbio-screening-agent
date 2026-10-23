from __future__ import annotations

from pathlib import Path

import pytest

from evals.dataset import DATASETS_DIR, DatasetError, load_suite
from evals.suites import SUITES
from tests.unit.eval_pipeline.helpers import write_jsonl

ROW = {"id": "a", "input": {"question": "q"}, "expected": "e"}


def test_loads_cases_in_order(tmp_path: Path) -> None:
    write_jsonl(
        tmp_path / "demo.jsonl",
        [ROW, {**ROW, "id": "b", "metadata": {"expected_verdict": "fail", "kind": "x"}}],
    )

    cases = load_suite("demo", tmp_path)

    assert [c.id for c in cases] == ["a", "b"]
    assert cases[0].expected_verdict == "pass"
    assert cases[1].expected_verdict == "fail"
    assert cases[1].metadata["kind"] == "x"


def test_blank_lines_are_ignored(tmp_path: Path) -> None:
    (tmp_path / "demo.jsonl").write_text('\n{"id": "a", "input": {}, "expected": "e"}\n\n', "utf-8")

    assert len(load_suite("demo", tmp_path)) == 1


def test_missing_suite_raises(tmp_path: Path) -> None:
    with pytest.raises(DatasetError, match="no_existe"):
        load_suite("no_existe", tmp_path)


@pytest.mark.parametrize(
    "bad_line",
    [
        "{no es json",
        '{"id": "b", "expected": "e"}',
        '{"id": "b", "input": {}, "expected": "e", "extra": 1}',
        '{"id": "b", "input": {}, "expected": "e", "metadata": {"expected_verdict": "maybe"}}',
    ],
)
def test_invalid_line_reports_line_number(tmp_path: Path, bad_line: str) -> None:
    (tmp_path / "demo.jsonl").write_text(
        '{"id": "a", "input": {}, "expected": "e"}\n' + bad_line + "\n", "utf-8"
    )

    with pytest.raises(DatasetError, match="línea 2"):
        load_suite("demo", tmp_path)


def test_duplicated_id_raises(tmp_path: Path) -> None:
    write_jsonl(tmp_path / "demo.jsonl", [ROW, ROW])

    with pytest.raises(DatasetError, match="repetido"):
        load_suite("demo", tmp_path)


@pytest.mark.parametrize("suite", sorted(SUITES))
def test_repo_suites_are_valid(suite: str) -> None:
    cases = load_suite(suite, DATASETS_DIR)

    assert cases


def test_judge_sanity_has_positive_and_negative_cases() -> None:
    cases = load_suite("judge_sanity")

    verdicts = [c.expected_verdict for c in cases]
    assert len(cases) >= 10
    assert verdicts.count("pass") >= 4
    assert verdicts.count("fail") >= 4
    assert all("question" in c.input and "answer" in c.input for c in cases)
