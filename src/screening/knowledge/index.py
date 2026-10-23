"""Índice versionado de la FAQ: fragmentos + embeddings + hash del corpus en un JSON.

Se reconstruye solo cuando cambia el hash, y entonces solo se embeben los fragmentos nuevos o
editados: arrancar el sistema nunca paga embeddings de la FAQ.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from screening.knowledge.chunking import KNOWLEDGE_DIR, FaqChunk
from screening.knowledge.embeddings import Embedder
from screening.ports import LLMUsage

DEFAULT_INDEX_PATH = KNOWLEDGE_DIR / "index.json"
_DECIMALS = 6  # suficiente para el coseno y deja el JSON a la mitad de tamaño


class KnowledgeIndexError(Exception):
    """El índice no existe, es inválido o fue construido con otro modelo."""


class IndexedChunk(BaseModel):
    id: str
    source: str
    text: str
    embedding: list[float]


class KnowledgeIndex(BaseModel):
    version: Literal[1] = 1
    model: str
    corpus_hash: str
    chunks: list[IndexedChunk]


@dataclass(frozen=True)
class BuildResult:
    index: KnowledgeIndex
    embedded: int
    usage: LLMUsage | None


def corpus_hash(chunks: Sequence[FaqChunk], model: str) -> str:
    digest = hashlib.sha256(model.encode())
    for chunk in chunks:
        digest.update(b"\0" + chunk.id.encode() + b"\0" + chunk.text.encode())
    return digest.hexdigest()


def build_index(
    chunks: Sequence[FaqChunk], embedder: Embedder, existing: KnowledgeIndex | None = None
) -> BuildResult:
    current_hash = corpus_hash(chunks, embedder.model)
    if existing is not None and existing.corpus_hash == current_hash:
        return BuildResult(index=existing, embedded=0, usage=None)

    known: dict[str, list[float]] = {}
    if existing is not None and existing.model == embedder.model:
        known = {c.text: c.embedding for c in existing.chunks}
    pending = list(dict.fromkeys(c.text for c in chunks if c.text not in known))

    usage = None
    if pending:
        result = embedder.embed(pending)
        usage = result.usage
        for text, vector in zip(pending, result.vectors, strict=True):
            known[text] = [round(x, _DECIMALS) for x in vector]

    index = KnowledgeIndex(
        model=embedder.model,
        corpus_hash=current_hash,
        chunks=[
            IndexedChunk(id=c.id, source=c.source, text=c.text, embedding=known[c.text])
            for c in chunks
        ],
    )
    return BuildResult(index=index, embedded=len(pending), usage=usage)


def load_index(path: Path | str = DEFAULT_INDEX_PATH) -> KnowledgeIndex:
    path = Path(path)
    try:
        return KnowledgeIndex.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise KnowledgeIndexError(
            f"no existe el índice {path}: correr `python -m screening.knowledge.build`"
        ) from e
    except ValidationError as e:
        raise KnowledgeIndexError(f"índice inválido en {path}:\n{e}") from e


def save_index(index: KnowledgeIndex, path: Path | str = DEFAULT_INDEX_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(index.model_dump_json() + "\n", encoding="utf-8")
