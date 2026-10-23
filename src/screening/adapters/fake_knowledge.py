"""`KnowledgeBase` falsa: coincidencia de palabras, para tests, evals y desarrollo hasta la 010."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from screening.ports import KnowledgeSnippet

_MIN_WORD = 4  # ignora artículos y preposiciones cortas


class FakeKnowledgeBase:
    def __init__(self, snippets: Iterable[KnowledgeSnippet | tuple[str, str]] = ()) -> None:
        self.snippets = [
            s if isinstance(s, KnowledgeSnippet) else KnowledgeSnippet(s[0], s[1], 0.0)
            for s in snippets
        ]
        self.queries: list[str] = []

    def search(self, query: str, *, limit: int = 3) -> list[KnowledgeSnippet]:
        self.queries.append(query)
        words = _words(query)
        scored = []
        for snippet in self.snippets:
            overlap = len(words & _words(snippet.text))
            if overlap:
                scored.append(KnowledgeSnippet(snippet.source, snippet.text, overlap / len(words)))
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:limit]


def _words(text: str) -> set[str]:
    plain = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    words = (w for w in re.findall(r"[a-z0-9]+", plain) if len(w) >= _MIN_WORD)
    # Singular aproximado para que "pagos" coincida con "pago".
    return {w[:-1] if w.endswith("s") and len(w) > _MIN_WORD else w for w in words}
