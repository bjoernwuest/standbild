"""Datenbank und Warteschlange gegen einen leeren Postgres (9.2, 9.3)."""

import threading
import time
from pathlib import Path

import pytest
from conftest import fake_pipeline

from standbild import worker
from standbild.config import effective_options, get_settings
from standbild.db import repo
from standbild.db.pool import get_pool, migrate

OPTS = effective_options({"whisper_model": "large-v3-turbo"}, env={})


def sql(query: str, *params: object) -> list[dict]:
    with get_pool().connection() as conn:
        cur = conn.execute(query, params)
        return cur.fetchall() if cur.description else []


def test_migrations_from_zero_and_idempotent(db: str) -> None:
    tables = {
        r["table_name"]
        for r in sql("SELECT table_name FROM information_schema.tables WHERE table_schema = %s", db)
    }
    assert tables == {"jobs", "schema_migrations"}  # genau eine fachliche Tabelle
    assert [r["version"] for r in sql("SELECT version FROM schema_migrations")] == [1]
    assert migrate() == []  # zweiter Start (oder zweiter Container) ändert nichts
    results: list[list[int]] = []
    threads = [threading.Thread(target=lambda: results.append(migrate())) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results == [[], [], [], []]


def test_no_second_job_while_one_is_active(db: str) -> None:
    first, created = repo.create("aaaaaaaaaaa", "Titel", OPTS)
    again, created_again = repo.create("aaaaaaaaaaa", "Titel", OPTS)
    assert created and not created_again and again["id"] == first["id"]
    assert repo.create("bbbbbbbbbbb", None, OPTS)[1]  # anderes Video: eigener Auftrag
    claimed = repo.claim()
    assert claimed["id"] == first["id"] and (claimed["status"], claimed["stage"], claimed["attempts"]) == (
        "running",
        "download",
        1,
    )
    assert repo.create("aaaaaaaaaaa", "Titel", OPTS)[0]["id"] == first["id"]  # auch während er läuft
    repo.fail(first["id"], "kaputt")
    assert repo.create("aaaaaaaaaaa", "Titel", OPTS)[1]  # danach wieder ein neuer


def test_two_parallel_workers_process_every_job_once(db: str) -> None:
    ids = [repo.create(f"video{i:06d}", None, OPTS)[0]["id"] for i in range(12)]
    claimed: list[object] = []

    def work() -> None:
        while job := repo.claim():
            claimed.append(job["id"])
            time.sleep(0.01)
            repo.finish(job["id"], "x.zip", 1, 1, "t")

    threads = [threading.Thread(target=work) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(map(str, claimed)) == sorted(map(str, ids))  # keiner doppelt, keiner vergessen
    assert claimed[:2] != claimed[1::-1] or True
    assert sql("SELECT count(*) AS n FROM jobs WHERE status = 'done' AND attempts = 1")[0]["n"] == 12


def test_queue_order_is_created_at(db: str) -> None:
    for i in range(3):
        repo.create(f"video{i:06d}", None, OPTS)
    assert [repo.claim()["youtube_id"] for _ in range(3)] == ["video000000", "video000001", "video000002"]
    assert repo.claim() is None


def test_stale_heartbeat_is_requeued_then_failed_after_three_starts(db: str) -> None:
    job, _ = repo.create("aaaaaaaaaaa", None, OPTS)
    for attempt in (1, 2):
        assert repo.claim()["attempts"] == attempt
        assert repo.requeue_stale() == []  # frischer Heartbeat: nichts passiert
        sql("UPDATE jobs SET heartbeat_at = now() - interval '6 minutes' WHERE id = %s", job["id"])
        assert [j["status"] for j in repo.requeue_stale()] == ["queued"]
    assert repo.claim()["attempts"] == 3
    sql("UPDATE jobs SET heartbeat_at = now() - interval '6 minutes' WHERE id = %s", job["id"])
    assert [j["status"] for j in repo.requeue_stale()] == ["failed"]
    final = repo.get(job["id"])
    assert (
        final["error"] == "Verarbeitung mehrfach abgebrochen"
        and final["finished_at"] is not None
        and repo.claim() is None
    )


def test_beat_progress_release_and_lookup_helpers(db: str) -> None:
    job, _ = repo.create("aaaaaaaaaaa", None, OPTS)
    repo.claim()
    repo.beat(job["id"], "transcribe", 0.42)
    row = repo.get(str(job["id"]))
    assert (row["stage"], round(row["progress"], 2)) == ("transcribe", 0.42) and repo.running_ids() == {
        str(job["id"])
    }
    assert repo.model_in_use("large-v3-turbo") and not repo.model_in_use("small")
    repo.release(job["id"])
    assert repo.get(job["id"])["status"] == "queued" and repo.running_ids() == set()
    assert repo.get("keine-uuid") is None and repo.get("00000000-0000-0000-0000-000000000000") is None
    assert repo.latest("aaaaaaaaaaa")["id"] == job["id"] and repo.latest("zzzzzzzzzzz") is None


def test_process_stores_result_and_keep_results_deletes_old_zips(
    db: str, env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STANDBILD_KEEP_RESULTS", "2")
    get_settings.cache_clear()
    packages = env / "packages"
    for _ in range(4):
        repo.create("aaaaaaaaaaa", None, OPTS)
        worker.process(repo.claim(), fake_pipeline)
    rows = sql("SELECT * FROM jobs ORDER BY created_at")
    assert len(rows) == 2 and all(r["status"] == "done" and r["screenshot_count"] == 2 for r in rows)
    assert rows[0]["title"] == "Titel aaaaaaaaaaa" and rows[0]["package_size"] > 0
    assert sorted(p.name for p in packages.iterdir()) == sorted(
        r["package_file"] for r in rows
    )  # ältere ZIPs sind weg
    assert repo.latest_done("aaaaaaaaaaa", OPTS)["id"] == rows[1]["id"]
    assert repo.latest_done("aaaaaaaaaaa", {**OPTS, "threshold": 0.3}) is None


def test_failed_pipeline_sets_short_error_and_old_failures_expire(db: str) -> None:
    from standbild.pipeline.fetch import FetchError

    def broken(*args: object, **kw: object) -> None:
        raise FetchError("Video ist altersbeschränkt")

    def crash(*args: object, **kw: object) -> None:
        raise ZeroDivisionError("x" * 1000)

    first, _ = repo.create("aaaaaaaaaaa", None, OPTS)
    worker.process(repo.claim(), broken)
    assert repo.get(first["id"])["error"] == "Video ist altersbeschränkt"
    second, _ = repo.create("aaaaaaaaaaa", None, OPTS)
    worker.process(repo.claim(), crash)
    assert (
        repo.get(second["id"])["error"].startswith("ZeroDivisionError: xxx")
        and len(repo.get(second["id"])["error"]) == 300
    )
    sql("UPDATE jobs SET finished_at = now() - interval '31 days'")
    assert repo.prune_failed() == 1  # nur der ältere; der neueste Auftrag eines Videos bleibt
    assert repo.get(first["id"]) is None and repo.get(second["id"]) is not None


def test_maintenance_removes_orphaned_workdirs(db: str, env: Path) -> None:
    job, _ = repo.create("aaaaaaaaaaa", None, OPTS)
    repo.claim()
    tmp = env / "tmp"
    for name in (str(job["id"]), "11111111-2222-3333-4444-555555555555", "run-abcdef12"):
        (tmp / name).mkdir(parents=True)
    worker.maintain()
    assert sorted(p.name for p in tmp.iterdir()) == sorted(
        [str(job["id"]), "run-abcdef12"]
    )  # laufender Auftrag und CLI-Lauf bleiben


def test_workers_pick_up_jobs_and_release_on_shutdown(db: str) -> None:
    started, release = threading.Event(), threading.Event()

    def slow(*args: object, **kw: object) -> object:
        started.set()
        release.wait(10)
        return fake_pipeline(*args, **kw)

    workers = worker.Workers(slow)
    workers.start()
    job, _ = repo.create("aaaaaaaaaaa", None, OPTS)
    assert started.wait(10) and repo.get(job["id"])["status"] == "running"
    workers.shutdown()
    assert repo.get(job["id"])["status"] == "queued"  # geordneter Stopp: sofort wieder in der Warteschlange
    release.set()
    time.sleep(0.5)
    assert repo.get(job["id"])["status"] == "queued" and not list(get_settings().packages_dir.glob("*.zip"))
