"""Objetivo de la suite `rag` (spec 010): recupera de la FAQ y redacta una respuesta corta.

La redacción es mínima, solo para evaluar la recuperación y el "no lo sé"; en producción la
respuesta la escribe el agente (009) usando la base de conocimiento como tool.
"""

from __future__ import annotations

from pathlib import Path

from evals.models import EvalCase, Target, TargetOutput
from screening.config import AppConfig
from screening.knowledge.base import EmbeddingKnowledgeBase
from screening.knowledge.embeddings import OpenAIEmbedder
from screening.knowledge.index import load_index
from screening.ports import ChatMessage, KnowledgeSnippet, LLMClient, LLMUsage

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "rag_answer.md"
NOT_FOUND_ANSWER = "No tengo esa información ahora mismo; lo consulto con el equipo y te aviso."


class RagAnswerer:
    def __init__(
        self, kb: EmbeddingKnowledgeBase, llm: LLMClient, *, model: str, system: str
    ) -> None:
        self.kb = kb
        self.llm = llm
        self.model = model
        self.system = system

    def __call__(self, case: EvalCase) -> TargetOutput:
        question = str(case.input["question"])
        snippets = self.kb.search(question)
        usages = [self.kb.last_usage] if self.kb.last_usage else []
        if not snippets:
            return TargetOutput(answer=NOT_FOUND_ANSWER, usage=_sum(usages))
        result = self.llm.generate_text(
            system=self.system,
            messages=[ChatMessage("user", _render(question, snippets))],
            model=self.model,
        )
        return TargetOutput(answer=result.text, usage=_sum([*usages, result.usage]))


def make_rag_target(llm: LLMClient, config: AppConfig) -> Target:
    embedder = OpenAIEmbedder.from_env(config.llm.embedding_model)
    kb = EmbeddingKnowledgeBase.from_index(load_index(), embedder)
    return RagAnswerer(
        kb, llm, model=config.llm.conversation_model, system=_load_prompt(PROMPT_PATH)
    )


def _render(question: str, snippets: list[KnowledgeSnippet]) -> str:
    faq = "\n\n".join(f"[{s.source}]\n{s.text}" for s in snippets)
    return f"Fragmentos de la FAQ:\n\n{faq}\n\nPregunta del candidato: {question}"


def _load_prompt(path: Path) -> str:
    """Quita el front matter (`--- version: N ---`)."""
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if text.startswith("---\n"):
        text = text.split("\n---\n", 1)[1]
    return text.strip()


def _sum(usages: list[LLMUsage]) -> LLMUsage | None:
    if not usages:
        return None
    return LLMUsage(
        model="+".join(dict.fromkeys(u.model for u in usages)),
        input_tokens=sum(u.input_tokens for u in usages),
        output_tokens=sum(u.output_tokens for u in usages),
        latency_ms=sum(u.latency_ms for u in usages),
        cost_usd=sum(u.cost_usd for u in usages),
    )
