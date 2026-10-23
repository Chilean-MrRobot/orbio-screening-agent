"""Fragmentación de la FAQ: un fragmento por pregunta (`## Pregunta` + su respuesta)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).resolve().parents[3] / "knowledge"
DEFAULT_FAQ_DIR = KNOWLEDGE_DIR / "faq"

_QUESTION = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


class FaqFormatError(ValueError):
    """La carpeta no existe o un documento tiene preguntas repetidas."""


@dataclass(frozen=True)
class FaqChunk:
    id: str
    source: str
    text: str


def slugify(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")


def split_document(name: str, content: str) -> list[FaqChunk]:
    """Lo anterior al primer `##` (título y aviso) no se indexa; preguntas sin respuesta tampoco."""
    stem = Path(name).stem
    content = content.replace("\r\n", "\n")
    headings = list(_QUESTION.finditer(content))
    chunks: list[FaqChunk] = []
    seen: set[str] = set()
    for i, heading in enumerate(headings):
        end = headings[i + 1].start() if i + 1 < len(headings) else len(content)
        question = heading.group(1)
        answer = content[heading.end() : end].strip()
        slug = slugify(question)
        if slug in seen:
            raise FaqFormatError(f"{name}: pregunta repetida {question!r}")
        seen.add(slug)
        if answer:
            chunks.append(
                FaqChunk(
                    id=f"{stem}#{slug}", source=f"{name} · {question}", text=f"{question}\n{answer}"
                )
            )
    return chunks


def load_chunks(faq_dir: Path | str = DEFAULT_FAQ_DIR) -> list[FaqChunk]:
    """Todos los `.md` de la carpeta, en orden alfabético para que el hash sea estable."""
    folder = Path(faq_dir)
    if not folder.is_dir():
        raise FaqFormatError(f"no existe la carpeta de la FAQ: {folder}")
    return [
        chunk
        for path in sorted(folder.glob("*.md"))
        for chunk in split_document(path.name, path.read_text(encoding="utf-8"))
    ]
