"""CLI: `python -m screening.knowledge.build [--check] [--force]`.

Reconstruye `knowledge/index.json` solo si cambió la FAQ (o el modelo de embeddings).
`--check` no llama a la API: sale con 1 si el índice falta o está desactualizado.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from screening.config import AppConfig, load_config
from screening.knowledge.chunking import DEFAULT_FAQ_DIR, load_chunks
from screening.knowledge.embeddings import Embedder
from screening.knowledge.index import (
    DEFAULT_INDEX_PATH,
    KnowledgeIndex,
    KnowledgeIndexError,
    build_index,
    corpus_hash,
    load_index,
    save_index,
)


def main(
    argv: Sequence[str] | None = None,
    *,
    embedder: Embedder | None = None,
    config: AppConfig | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m screening.knowledge.build", description=__doc__
    )
    parser.add_argument("--faq-dir", default=str(DEFAULT_FAQ_DIR))
    parser.add_argument("--index", default=str(DEFAULT_INDEX_PATH))
    parser.add_argument("--check", action="store_true", help="solo verifica, sin llamar a la API")
    parser.add_argument("--force", action="store_true", help="reembebe todos los fragmentos")
    args = parser.parse_args(argv)

    config = config or load_config()
    model = embedder.model if embedder else config.llm.embedding_model
    chunks = load_chunks(args.faq_dir)
    existing = _try_load(args.index)

    if args.check:
        if existing is not None and existing.corpus_hash == corpus_hash(chunks, model):
            print(f"Índice al día: {len(chunks)} fragmentos ({model}).")
            return 0
        print(f"Índice ausente o desactualizado: correr `{parser.prog}`.")
        return 1

    if embedder is None:
        from screening.knowledge.embeddings import OpenAIEmbedder

        embedder = OpenAIEmbedder.from_env(model)
    result = build_index(chunks, embedder, None if args.force else existing)
    if result.index is existing:
        print(f"Índice al día: {len(chunks)} fragmentos, sin llamadas a la API.")
        return 0

    save_index(result.index, args.index)
    usage = result.usage
    tokens, cost = (usage.input_tokens, usage.cost_usd) if usage else (0, 0.0)
    print(
        f"Índice guardado en {args.index}: {len(chunks)} fragmentos, {result.embedded} embebidos "
        f"({tokens} tokens, ${cost:.6f})."
    )
    return 0


def _try_load(path: str) -> KnowledgeIndex | None:
    try:
        return load_index(path)
    except KnowledgeIndexError:
        return None


if __name__ == "__main__":
    sys.exit(main())
