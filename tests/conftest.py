"""Fixtures compartidos. Agregar aquí los reutilizables; no duplicarlos por archivo."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from screening.adapters.memory import InMemoryCandidateRepository
from screening.clock import FixedClock
from screening.config import AppConfig, load_config
from screening.config.loader import DEFAULT_CONFIG_DIR
from screening.domain import ApplicationPayload, Candidate, Country


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 10, 22, 10, 0, tzinfo=UTC))


@pytest.fixture(scope="session")
def app_config() -> AppConfig:
    return load_config(DEFAULT_CONFIG_DIR)


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """Copia editable del `config/` del repo, para tests que rompen la configuración."""
    target = tmp_path / "config"
    shutil.copytree(DEFAULT_CONFIG_DIR, target)
    return target


@pytest.fixture
def repository() -> InMemoryCandidateRepository:
    return InMemoryCandidateRepository()


@pytest.fixture
def candidate() -> Candidate:
    application = ApplicationPayload(
        application_id="app-001",
        source="infojobs",
        full_name="Lucía Pérez",
        phone="+34600111222",
        country=Country.ES,
        city="Madrid",
    )
    return Candidate(application=application, phone="+34600111222", country=Country.ES)
