from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from screening.knowledge.chunking import DEFAULT_FAQ_DIR, FaqChunk, load_chunks
from screening.knowledge.index import (
    DEFAULT_INDEX_PATH,
    KnowledgeIndexError,
    build_index,
    corpus_hash,
    load_index,
    save_index,
)
from tests.unit.knowledge.conftest import FakeEmbedder

CHUNKS = [
    FaqChunk(id="pago#espana", source="pago.md · ¿España?", text="¿España?\nDe 9 a 12 €."),
    FaqChunk(id="pago#propinas", source="pago.md · ¿Propinas?", text="¿Propinas?\nÍntegras."),
    FaqChunk(id="turnos#finde", source="turnos.md · ¿Finde?", text="¿Finde?\nSí."),
]


def test_hash_is_deterministic() -> None:
    assert corpus_hash(CHUNKS, "m") == corpus_hash(list(CHUNKS), "m")


@pytest.mark.parametrize(
    "changed",
    [
        [replace(CHUNKS[0], text="¿España?\nDe 10 a 13 €."), *CHUNKS[1:]],
        [replace(CHUNKS[0], id="pago#es"), *CHUNKS[1:]],
        CHUNKS[:2],
    ],
    ids=["text", "id", "removed"],
)
def test_hash_changes_with_the_content(changed: list[FaqChunk]) -> None:
    assert corpus_hash(changed, "m") != corpus_hash(CHUNKS, "m")


def test_hash_changes_with_the_model() -> None:
    assert corpus_hash(CHUNKS, "a") != corpus_hash(CHUNKS, "b")


def test_build_embeds_every_chunk(embedder: FakeEmbedder) -> None:
    result = build_index(CHUNKS, embedder)

    assert result.embedded == 3
    assert embedder.embedded_texts == [c.text for c in CHUNKS]
    assert result.index.model == embedder.model
    assert result.index.corpus_hash == corpus_hash(CHUNKS, embedder.model)
    assert [c.id for c in result.index.chunks] == [c.id for c in CHUNKS]
    assert result.usage is not None and result.usage.cost_usd > 0


def test_unchanged_corpus_reuses_the_index_without_calls(embedder: FakeEmbedder) -> None:
    existing = build_index(CHUNKS, embedder).index
    embedder.calls.clear()

    result = build_index(CHUNKS, embedder, existing)

    assert result.index is existing
    assert result.embedded == 0
    assert result.usage is None
    assert embedder.calls == []


def test_only_changed_chunks_are_embedded(embedder: FakeEmbedder) -> None:
    existing = build_index(CHUNKS, embedder).index
    embedder.calls.clear()
    edited = [CHUNKS[0], replace(CHUNKS[1], text="¿Propinas?\nSon tuyas."), CHUNKS[2]]

    result = build_index(edited, embedder, existing)

    assert embedder.embedded_texts == ["¿Propinas?\nSon tuyas."]
    assert result.embedded == 1
    assert result.index.corpus_hash != existing.corpus_hash
    assert result.index.chunks[0].embedding == existing.chunks[0].embedding


def test_model_change_embeds_everything(embedder: FakeEmbedder) -> None:
    existing = build_index(CHUNKS, FakeEmbedder(model="otro")).index

    result = build_index(CHUNKS, embedder, existing)

    assert result.embedded == 3
    assert result.index.model == embedder.model


def test_empty_corpus_builds_an_empty_index(embedder: FakeEmbedder) -> None:
    result = build_index([], embedder)

    assert result.index.chunks == []
    assert embedder.calls == []


def test_save_and_load_round_trip(tmp_path: Path, embedder: FakeEmbedder) -> None:
    index = build_index(CHUNKS, embedder).index
    path = tmp_path / "sub" / "index.json"

    save_index(index, path)

    assert load_index(path) == index


def test_load_missing_index_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(KnowledgeIndexError, match="no existe"):
        load_index(tmp_path / "index.json")


def test_load_invalid_index_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "index.json"
    path.write_text('{"version": 1}', encoding="utf-8")

    with pytest.raises(KnowledgeIndexError, match="inválido"):
        load_index(path)


@pytest.mark.skipif(not DEFAULT_INDEX_PATH.exists(), reason="el índice aún no se construyó")
def test_versioned_index_is_up_to_date_with_the_faq(app_config) -> None:
    """Si falla: `python -m screening.knowledge.build` y subir `knowledge/index.json`."""
    index = load_index(DEFAULT_INDEX_PATH)

    assert index.model == app_config.llm.embedding_model
    assert index.corpus_hash == corpus_hash(load_chunks(DEFAULT_FAQ_DIR), index.model)
