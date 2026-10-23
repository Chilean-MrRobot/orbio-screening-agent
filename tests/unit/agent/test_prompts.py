from pathlib import Path

import pytest

from screening.agent.prompts import (
    DEFAULT_PROMPTS_DIR,
    PROMPT_NAMES,
    PromptError,
    load_agent_prompts,
    load_prompt,
)


def _copy_prompts(tmp_path: Path) -> Path:
    for name in PROMPT_NAMES:
        source = DEFAULT_PROMPTS_DIR / f"{name}.md"
        (tmp_path / f"{name}.md").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


def test_repo_prompts_load_with_versions() -> None:
    prompts = load_agent_prompts()

    assert prompts.system.version.startswith("v1-")
    assert "system=v1-" in prompts.version
    assert prompts.system.placeholders >= {"agent_name", "client_name", "license_term"}
    assert prompts.extraction.placeholders == set()
    assert prompts.reply.placeholders == {"sla_hours"}


def test_render_replaces_placeholders(tmp_path: Path) -> None:
    path = tmp_path / "p.md"
    path.write_text("---\nversion: 2\n---\nHola {{name}}, soy {{agent}}.", encoding="utf-8")

    prompt = load_prompt(path)

    assert prompt.render(name="Ana", agent="Sofía") == "Hola Ana, soy Sofía."
    assert prompt.version.startswith("v2-")


def test_render_with_missing_value_fails(tmp_path: Path) -> None:
    path = tmp_path / "p.md"
    path.write_text("---\nversion: 1\n---\nHola {{name}}", encoding="utf-8")

    with pytest.raises(PromptError, match="name"):
        load_prompt(path).render()


@pytest.mark.parametrize(
    "content", ["sin front matter", "---\ntitle: x\n---\ncuerpo"], ids=["no-front", "no-version"]
)
def test_prompt_without_version_fails(tmp_path: Path, content: str) -> None:
    path = tmp_path / "p.md"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(PromptError):
        load_prompt(path)


def test_missing_prompt_file_fails(tmp_path: Path) -> None:
    with pytest.raises(PromptError, match="falta"):
        load_agent_prompts(tmp_path)


def test_agent_fails_at_startup_with_unknown_placeholder(tmp_path: Path, make_agent) -> None:
    prompts_dir = _copy_prompts(tmp_path)
    reply = prompts_dir / "reply.md"
    reply.write_text(reply.read_text(encoding="utf-8") + "\n{{no_existe}}", encoding="utf-8")

    with pytest.raises(PromptError, match="no_existe"):
        make_agent(prompts_dir=prompts_dir)
