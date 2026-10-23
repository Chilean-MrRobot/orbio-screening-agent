"""Carga y valida la configuración de negocio desde `config/`."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from screening.config.models import AppConfig

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"


class ConfigError(Exception):
    """La configuración no existe o no es válida; el sistema no debe arrancar."""


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise ConfigError(f"falta el archivo de configuración: {path}") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"YAML inválido en {path}: {e}") from e
    if not isinstance(content, dict):
        raise ConfigError(f"{path} debe contener un mapa en la raíz")
    return content


def _section(path: Path, key: str) -> Any:
    content = _read_yaml(path)
    if key not in content:
        raise ConfigError(f"{path} debe tener la clave raíz {key!r}")
    return content[key]


def load_config(config_dir: Path | str | None = None) -> AppConfig:
    base = Path(config_dir or os.environ.get("SCREENING_CONFIG_DIR") or DEFAULT_CONFIG_DIR)
    client_file = _read_yaml(base / "client.yaml")

    regions: dict[str, Any] = {}
    for region_path in sorted((base / "regions").glob("*.yaml")):
        region = _section(region_path, "region")
        regions[region.get("country")] = region

    raw = {
        "client": client_file.get("client"),
        "llm": client_file.get("llm"),
        "regions": regions,
        "knockout_rules": _section(base / "rules" / "knockout.yaml", "rules"),
        "scoring": _section(base / "scoring.yaml", "scoring"),
        "cadences": _read_yaml(base / "cadences.yaml"),
        "templates": _section(base / "templates.yaml", "templates"),
    }
    try:
        return AppConfig.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(f"configuración inválida en {base}:\n{e}") from e
