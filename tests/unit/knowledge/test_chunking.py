from __future__ import annotations

from pathlib import Path

import pytest

from screening.knowledge.chunking import (
    DEFAULT_FAQ_DIR,
    FaqFormatError,
    load_chunks,
    slugify,
    split_document,
)

DOC = """# Pago

> Contenido ficticio para el demo.

## ¿Cuánto se paga en España?

De 9 a 12 € por hora.
Según la franja.

## ¿Las propinas son para el repartidor?

Sí, íntegras.

## ¿Cada cuánto se cobra?

Mensual.
"""


def test_one_chunk_per_question_without_title_or_notice() -> None:
    chunks = split_document("pago.md", DOC)

    assert [c.id for c in chunks] == [
        "pago#cuanto-se-paga-en-espana",
        "pago#las-propinas-son-para-el-repartidor",
        "pago#cada-cuanto-se-cobra",
    ]
    assert all("ficticio" not in c.text for c in chunks)


def test_chunk_text_is_question_plus_answer() -> None:
    first = split_document("pago.md", DOC)[0]

    assert first.text == "¿Cuánto se paga en España?\nDe 9 a 12 € por hora.\nSegún la franja."
    assert first.source == "pago.md · ¿Cuánto se paga en España?"


def test_slug_is_stable_and_ascii() -> None:
    assert slugify("¿Qué licencia de manejo necesito en México?") == (
        "que-licencia-de-manejo-necesito-en-mexico"
    )


def test_question_without_answer_is_skipped() -> None:
    chunks = split_document("x.md", "# X\n\n## ¿Vacía?\n\n## ¿Llena?\n\nSí.\n")

    assert [c.id for c in chunks] == ["x#llena"]


def test_repeated_question_is_an_error() -> None:
    with pytest.raises(FaqFormatError, match="repetida"):
        split_document("x.md", "## ¿Hola?\n\nUno.\n\n## Hola\n\nDos.\n")


def test_load_chunks_reads_markdown_files_in_order(tmp_path: Path) -> None:
    (tmp_path / "b.md").write_text("## ¿B?\n\nbe\n", encoding="utf-8")
    (tmp_path / "a.md").write_text("## ¿A?\n\na\n", encoding="utf-8")
    (tmp_path / "notas.txt").write_text("## ¿Ignorado?\n\nno\n", encoding="utf-8")

    assert [c.id for c in load_chunks(tmp_path)] == ["a#a", "b#b"]


def test_load_chunks_requires_the_folder(tmp_path: Path) -> None:
    with pytest.raises(FaqFormatError, match="no existe"):
        load_chunks(tmp_path / "falta")


def test_repository_faq_has_a_reasonable_size() -> None:
    chunks = load_chunks(DEFAULT_FAQ_DIR)

    assert 15 <= len(chunks) <= 30
    assert len({c.id for c in chunks}) == len(chunks)
