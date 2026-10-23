from screening.adapters.fake_knowledge import FakeKnowledgeBase
from screening.ports import KnowledgeSnippet


def test_search_ranks_by_word_overlap_ignoring_accents() -> None:
    kb = FakeKnowledgeBase(
        [
            ("pagos", "Los pagos se hacen cada semana."),
            ("propinas", "Las propinas son íntegras para el repartidor."),
            KnowledgeSnippet("uniforme", "Damos uniforme y mochila térmica.", 0.0),
        ]
    )

    results = kb.search("¿Las propinas son para el repartidor?")

    assert [r.source for r in results] == ["propinas"]
    assert results[0].score > 0
    assert kb.queries == ["¿Las propinas son para el repartidor?"]
    assert kb.search("mochila termica")[0].source == "uniforme"


def test_search_without_matches_or_snippets_is_empty() -> None:
    assert FakeKnowledgeBase().search("¿Pagan?") == []
    assert FakeKnowledgeBase([("a", "texto")]).search("nada que ver") == []


def test_search_respects_limit() -> None:
    kb = FakeKnowledgeBase([(f"s{i}", "horario flexible") for i in range(5)])

    assert len(kb.search("horario", limit=2)) == 2
