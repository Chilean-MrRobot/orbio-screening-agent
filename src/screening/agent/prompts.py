"""Prompts versionados del agente (`prompts/agent/*.md`): front matter con `version` + cuerpo."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts" / "agent"
PROMPT_NAMES = ("system", "extraction", "reply")

_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


class PromptError(Exception):
    """El prompt no existe, no declara versión o le faltan valores al renderizarlo."""


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    body: str
    version: str

    @property
    def placeholders(self) -> set[str]:
        return set(_PLACEHOLDER.findall(self.body))

    def render(self, **values: object) -> str:
        missing = self.placeholders - set(values)
        if missing:
            raise PromptError(f"al prompt {self.name!r} le faltan valores: {sorted(missing)}")
        return _PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), self.body)


@dataclass(frozen=True)
class AgentPrompts:
    system: PromptTemplate
    extraction: PromptTemplate
    reply: PromptTemplate

    @property
    def version(self) -> str:
        """Versión conjunta para trazas y reportes: `system=v1,extraction=v1,reply=v1`."""
        return ",".join(f"{p.name}={p.version}" for p in (self.system, self.extraction, self.reply))


def load_prompt(path: Path) -> PromptTemplate:
    try:
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    except FileNotFoundError as e:
        raise PromptError(f"falta el prompt {path}") from e
    front = _FRONT_MATTER.match(text)
    if not front:
        raise PromptError(f"{path} debe empezar con front matter (--- version: N ---)")
    meta = yaml.safe_load(front.group(1)) or {}
    if "version" not in meta:
        raise PromptError(f"el front matter de {path} debe declarar 'version'")
    body = text[front.end() :].strip()
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return PromptTemplate(name=path.stem, body=body, version=f"v{meta['version']}-{digest}")


def load_agent_prompts(prompts_dir: Path | str = DEFAULT_PROMPTS_DIR) -> AgentPrompts:
    base = Path(prompts_dir)
    return AgentPrompts(**{name: load_prompt(base / f"{name}.md") for name in PROMPT_NAMES})
