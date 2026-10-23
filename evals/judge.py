"""Juez LLM-as-a-judge: compara la respuesta del sistema con la esperada."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml

from evals.models import EvalCase, JudgeVerdict
from screening.ports import ChatMessage, LLMClient, LLMUsage

DEFAULT_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "judge.md"
PLACEHOLDERS = ("question", "expected", "answer")

_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_SECTION = re.compile(r"^## (system|user)\s*$", re.MULTILINE)


class Judge:
    """Usa el prompt versionado en `prompts/judge.md` (front matter + secciones system/user)."""

    def __init__(
        self, llm: LLMClient, model: str, prompt_path: Path | str = DEFAULT_PROMPT_PATH
    ) -> None:
        self.llm = llm
        self.model = model
        text = Path(prompt_path).read_text(encoding="utf-8").replace("\r\n", "\n")
        self.system, self.user_template, version = _parse_prompt(text)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
        self.prompt_version = f"v{version}-{digest}"

    def judge(self, case: EvalCase, answer: str) -> tuple[JudgeVerdict, LLMUsage]:
        user = self.user_template
        for name, value in (
            ("question", _question(case)),
            ("expected", case.expected),
            ("answer", answer),
        ):
            user = user.replace("{{" + name + "}}", value)
        result = self.llm.generate_structured(
            system=self.system,
            messages=[ChatMessage("user", user)],
            schema=JudgeVerdict,
            model=self.model,
        )
        return result.output, result.usage


def _question(case: EvalCase) -> str:
    question = case.input.get("question")
    if isinstance(question, str):
        return question
    return json.dumps(case.input, ensure_ascii=False, indent=2)


def _parse_prompt(text: str) -> tuple[str, str, str]:
    front = _FRONT_MATTER.match(text)
    if not front:
        raise ValueError("el prompt del juez debe empezar con front matter (--- version: N ---)")
    meta = yaml.safe_load(front.group(1)) or {}
    if "version" not in meta:
        raise ValueError("el front matter del prompt del juez debe declarar 'version'")

    parts = _SECTION.split(text[front.end() :])
    sections = dict(zip(parts[1::2], (p.strip() for p in parts[2::2]), strict=True))
    if set(sections) != {"system", "user"}:
        raise ValueError("el prompt del juez debe tener las secciones '## system' y '## user'")
    for name in PLACEHOLDERS:
        if "{{" + name + "}}" not in sections["user"]:
            raise ValueError(f"falta el placeholder {{{{{name}}}}} en la sección user")
    return sections["system"], sections["user"], str(meta["version"])
