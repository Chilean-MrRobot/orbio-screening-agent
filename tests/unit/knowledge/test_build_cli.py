from __future__ import annotations

from pathlib import Path

import pytest

from screening.knowledge.build import main
from screening.knowledge.index import load_index
from tests.unit.knowledge.conftest import FakeEmbedder


@pytest.fixture
def faq_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "faq"
    folder.mkdir()
    (folder / "pago.md").write_text(
        "# Pago\n\n## ¿Cuánto pagan?\n\nDe 9 a 12 €.\n\n## ¿Propinas?\n\nÍntegras.\n",
        encoding="utf-8",
    )
    return folder


def _run(faq_dir: Path, embedder: FakeEmbedder, *flags: str) -> int:
    index = faq_dir.parent / "index.json"
    return main(["--faq-dir", str(faq_dir), "--index", str(index), *flags], embedder=embedder)


def test_builds_the_index(faq_dir: Path, embedder: FakeEmbedder) -> None:
    assert _run(faq_dir, embedder) == 0

    index = load_index(faq_dir.parent / "index.json")
    assert [c.id for c in index.chunks] == ["pago#cuanto-pagan", "pago#propinas"]
    assert index.model == embedder.model


def test_second_run_does_not_call_the_api(faq_dir: Path, embedder: FakeEmbedder) -> None:
    _run(faq_dir, embedder)
    embedder.calls.clear()

    assert _run(faq_dir, embedder) == 0
    assert embedder.calls == []


def test_force_rebuilds_everything(faq_dir: Path, embedder: FakeEmbedder) -> None:
    _run(faq_dir, embedder)
    embedder.calls.clear()

    assert _run(faq_dir, embedder, "--force") == 0
    assert len(embedder.embedded_texts) == 2


def test_check_without_index_fails(faq_dir: Path, embedder: FakeEmbedder) -> None:
    assert _run(faq_dir, embedder, "--check") == 1
    assert embedder.calls == []


def test_check_detects_a_stale_index(faq_dir: Path, embedder: FakeEmbedder) -> None:
    _run(faq_dir, embedder)
    assert _run(faq_dir, embedder, "--check") == 0

    (faq_dir / "pago.md").write_text("## ¿Cuánto pagan?\n\nDe 10 a 13 €.\n", encoding="utf-8")
    embedder.calls.clear()

    assert _run(faq_dir, embedder, "--check") == 1
    assert embedder.calls == []
