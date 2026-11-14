"""Gemeinsame Fixtures: Verzeichnisse statt Volumes, leerer Postgres, Pipeline- und Modell-Attrappen."""

from __future__ import annotations

import os
import uuid
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from standbild import models, youtube
from standbild.config import get_settings
from standbild.pipeline import Result

MODEL_FILES = ("model.bin", "config.json", "tokenizer.json")


def fake_model(directory: Path) -> Path:
    directory.mkdir(parents=True)
    for name in MODEL_FILES:
        (directory / name).write_bytes(b"x" * 100)
    return directory


@pytest.fixture(autouse=True)
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Jeder Test bekommt eigene "Volumes" und eine Seed-Ausstattung mit einem Attrappen-Modell."""
    for key in [k for k in os.environ if k.startswith("STANDBILD_") and k != "STANDBILD_TEST_DB"]:
        monkeypatch.delenv(key)
    for name in ("tmp", "packages", "models"):
        monkeypatch.setenv(f"STANDBILD_{name.upper()}_DIR", str(tmp_path / name))
    monkeypatch.setenv("STANDBILD_SEED_DIR", str(tmp_path / "seed"))
    fake_model(tmp_path / "seed" / "large-v3-turbo")
    monkeypatch.setattr(youtube, "_video_info", lambda youtube_id: {"title": f"Titel {youtube_id}"})
    get_settings.cache_clear()
    models._downloads.clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def postgres() -> Iterator[dict[str, Any]]:
    """Leerer Postgres aus testcontainers; STANDBILD_TEST_DB (libpq-URL) nutzt stattdessen einen vorhandenen."""
    from psycopg.conninfo import conninfo_to_dict

    if url := os.environ.get("STANDBILD_TEST_DB"):
        yield conninfo_to_dict(url)
        return
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as pg:
        yield {
            "host": pg.get_container_host_ip(),
            "port": pg.get_exposed_port(5432),
            "dbname": pg.dbname,
            "user": pg.username,
            "password": pg.password,
        }


@pytest.fixture
def db(postgres: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Frisches, noch nicht vorhandenes Schema: die Migrationen laufen in jedem Test von null."""
    from standbild.db.pool import close_pool, migrate

    schema = f"t_{uuid.uuid4().hex[:12]}"
    for key, value in {**postgres, "name": postgres["dbname"], "schema": schema}.items():
        monkeypatch.setenv(f"STANDBILD_DB_{key.upper()}", str(value))
    get_settings.cache_clear()
    migrate()
    models.startup()
    yield schema
    close_pool()


def fake_pipeline(source: str, opts: dict[str, Any], *, workdir: Path, out_dir: Path, **kw: Any) -> Result:
    """Attrappe für run_pipeline: legt sofort ein kleines ZIP ab."""
    video_id = source.rsplit("=", 1)[-1]
    out_dir.mkdir(parents=True, exist_ok=True)
    kw.get("progress", lambda *a: None)("detect", 0.5)
    kw.get("on_meta", lambda m: None)({"title": f"Titel {video_id}"})
    package = out_dir / f"{video_id}_{uuid.uuid4().hex[:6]}.zip"
    with zipfile.ZipFile(package, "w") as z:
        z.writestr("meta.json", '{"channel": "Testkanal", "duration": 61.0}')
        z.writestr("transcript.md", "# Test\n")
    return Result(package, video_id, f"Titel {video_id}", 2)
