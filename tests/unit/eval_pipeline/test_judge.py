from __future__ import annotations

from pathlib import Path

import pytest

from evals.judge import DEFAULT_PROMPT_PATH, Judge
from evals.models import JudgeVerdict
from screening.adapters.fake_llm import FakeLLMClient
from tests.unit.eval_pipeline.helpers import make_case, verdict


def test_judge_sends_question_expected_and_answer() -> None:
    llm = FakeLLMClient([verdict("pass", 4)])
    judge = Judge(llm, "judge-model")

    result, usage = judge.judge(make_case(), "Sí, sábados y domingos.")

    assert result == verdict("pass", 4)
    assert usage.model == "judge-model"
    call = llm.calls[0]
    assert call.method == "structured"
    assert call.schema is JudgeVerdict
    assert call.model == "judge-model"
    assert call.temperature is None
    user = call.messages[-1].content
    assert "¿Puedo trabajar solo fines de semana?" in user
    assert "Sí, hay turnos de fin de semana." in user
    assert "Sí, sábados y domingos." in user
    assert "{{" not in user


def test_input_without_question_is_shown_as_json() -> None:
    llm = FakeLLMClient([verdict()])
    case = make_case(conversation=[{"role": "user", "content": "Hola"}])

    Judge(llm, "m").judge(case, "respuesta")

    assert '"conversation"' in llm.calls[0].messages[-1].content


def test_prompt_version_has_declared_version_and_hash() -> None:
    judge = Judge(FakeLLMClient(), "m")

    version, digest = judge.prompt_version.split("-")
    assert version.startswith("v")
    assert len(digest) == 8


def test_repo_prompt_has_system_instructions() -> None:
    judge = Judge(FakeLLMClient(), "m", DEFAULT_PROMPT_PATH)

    assert "pass" in judge.system and "fail" in judge.system


def test_prompt_without_placeholders_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "judge.md"
    path.write_text("---\nversion: 1\n---\n## system\nJuez\n## user\n{{question}}\n", "utf-8")

    with pytest.raises(ValueError, match="expected"):
        Judge(FakeLLMClient(), "m", path)


def test_score_must_be_between_1_and_5() -> None:
    with pytest.raises(ValueError):
        JudgeVerdict(reasoning="r", verdict="pass", score=6)
