"""Tabelle jobs: Warteschlange und Verzeichnis der Ergebnisse (Abschnitt 9)."""

from __future__ import annotations

import uuid
from typing import Any

from psycopg.types.json import Jsonb

from standbild.config import FAILED_RETENTION_DAYS, MAX_ATTEMPTS, STALE_AFTER
from standbild.db.pool import get_pool

Job = dict[str, Any]
_ACTIVE = "status IN ('queued', 'running')"


def _one(query: str, *params: Any) -> Job | None:
    with get_pool().connection() as conn:
        return conn.execute(query, params).fetchone()


def _all(query: str, *params: Any) -> list[Job]:
    with get_pool().connection() as conn:
        return conn.execute(query, params).fetchall()


def create(youtube_id: str, title: str | None, options: dict[str, Any]) -> tuple[Job, bool]:
    """Legt einen Auftrag an; wartet oder läuft schon einer für das Video, kommt dieser zurück (Job, neu?)."""
    for _ in range(3):  # der bestehende Auftrag kann zwischen INSERT und SELECT fertig werden
        job = _one(
            f"INSERT INTO jobs (youtube_id, title, options) VALUES (%s, %s, %s) "
            f"ON CONFLICT (youtube_id) WHERE {_ACTIVE} DO NOTHING RETURNING *",
            youtube_id, title, Jsonb(options),
        )  # fmt: skip
        if job:
            return job, True
        job = _one(f"SELECT * FROM jobs WHERE youtube_id = %s AND {_ACTIVE}", youtube_id)
        if job:
            return job, False
    raise RuntimeError("Auftrag konnte nicht angelegt werden")


def claim() -> Job | None:
    """Holt den ältesten wartenden Auftrag; parallele Worker überspringen gesperrte Zeilen."""
    return _one(
        "UPDATE jobs SET status = 'running', stage = 'download', progress = 0, attempts = attempts + 1, "
        "started_at = now(), heartbeat_at = now(), error = NULL "
        "WHERE id = (SELECT id FROM jobs WHERE status = 'queued' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1) "
        "RETURNING *"
    )


def beat(job_id: Any, stage: str, progress: float) -> None:
    _one("UPDATE jobs SET heartbeat_at = now(), stage = %s, progress = %s WHERE id = %s AND status = 'running' RETURNING id",
         stage, progress, job_id)  # fmt: skip


def set_title(job_id: Any, title: str | None) -> None:
    _one("UPDATE jobs SET title = %s WHERE id = %s RETURNING id", title, job_id)


def finish(job_id: Any, package_file: str, package_size: int, screenshot_count: int, title: str) -> bool:
    """Setzt den Auftrag auf done – erst nachdem das ZIP unter seinem endgültigen Namen liegt."""
    return bool(
        _one(
            "UPDATE jobs SET status = 'done', stage = NULL, progress = 1, finished_at = now(), package_file = %s, "
            "package_size = %s, screenshot_count = %s, title = %s WHERE id = %s AND status = 'running' RETURNING id",
            package_file,
            package_size,
            screenshot_count,
            title,
            job_id,
        )  # fmt: skip
    )


def fail(job_id: Any, error: str) -> None:
    _one("UPDATE jobs SET status = 'failed', stage = NULL, finished_at = now(), error = %s "
         "WHERE id = %s AND status = 'running' RETURNING id", error, job_id)  # fmt: skip


def requeue_stale() -> list[Job]:
    """Aufträge ohne Lebenszeichen zurück in die Warteschlange; nach MAX_ATTEMPTS Starts endgültig failed."""
    return _all(
        "UPDATE jobs SET status = CASE WHEN attempts >= %s THEN 'failed' ELSE 'queued' END, stage = NULL, "
        "finished_at = CASE WHEN attempts >= %s THEN now() END, "
        "error = CASE WHEN attempts >= %s THEN 'Verarbeitung mehrfach abgebrochen' END "
        "WHERE status = 'running' AND heartbeat_at < now() - make_interval(secs => %s) RETURNING *",
        MAX_ATTEMPTS, MAX_ATTEMPTS, MAX_ATTEMPTS, STALE_AFTER,
    )  # fmt: skip


def get(job_id: Any) -> Job | None:
    try:
        return _one("SELECT * FROM jobs WHERE id = %s", uuid.UUID(str(job_id)))
    except ValueError:
        return None


def latest(youtube_id: str) -> Job | None:
    return _one("SELECT * FROM jobs WHERE youtube_id = %s ORDER BY created_at DESC LIMIT 1", youtube_id)


def latest_done(youtube_id: str, options: dict[str, Any]) -> Job | None:
    """Neuestes erfolgreiches Ergebnis mit denselben wirksamen Parametern."""
    return _one(
        "SELECT * FROM jobs WHERE youtube_id = %s AND status = 'done' AND options = %s ORDER BY created_at DESC LIMIT 1",
        youtube_id, Jsonb(options),
    )  # fmt: skip


def recent(limit: int) -> list[Job]:
    return _all("SELECT * FROM jobs ORDER BY created_at DESC LIMIT %s", limit)


def prune(youtube_id: str, keep: int) -> list[str]:
    """Löscht abgeschlossene Aufträge eines Videos bis auf die ``keep`` neuesten; liefert deren ZIP-Namen."""
    rows = _all(
        "DELETE FROM jobs WHERE id IN (SELECT id FROM jobs WHERE youtube_id = %s AND status IN ('done', 'failed') "
        "ORDER BY created_at DESC OFFSET %s) RETURNING package_file",
        youtube_id, keep,
    )  # fmt: skip
    return [r["package_file"] for r in rows if r["package_file"]]


def prune_failed() -> int:
    """Fehlgeschlagene Aufträge nach 30 Tagen löschen, außer es ist der neueste eines Videos."""
    return len(
        _all(
            "DELETE FROM jobs j WHERE status = 'failed' AND finished_at < now() - make_interval(days => %s) "
            "AND EXISTS (SELECT 1 FROM jobs n WHERE n.youtube_id = j.youtube_id AND n.created_at > j.created_at) "
            "RETURNING id",
            FAILED_RETENTION_DAYS,
        )
    )


def running_ids() -> set[str]:
    return {str(r["id"]) for r in _all("SELECT id FROM jobs WHERE status = 'running'")}


def model_in_use(name: str) -> bool:
    return bool(
        _one(f"SELECT 1 AS x FROM jobs WHERE {_ACTIVE} AND options->>'whisper_model' = %s LIMIT 1", name)
    )


def release(job_id: Any) -> None:
    """Laufenden Auftrag beim geordneten Stopp zurück in die Warteschlange."""
    _one(
        "UPDATE jobs SET status = 'queued', stage = NULL WHERE id = %s AND status = 'running' RETURNING id",
        job_id,
    )
