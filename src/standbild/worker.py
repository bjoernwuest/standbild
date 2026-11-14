"""Auftragsverwaltung: anlegen/wiederverwenden, Warteschlange, Heartbeat, Wiederaufnahme, Aufräumen (9.3)."""

from __future__ import annotations

import logging
import shutil
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from standbild import models
from standbild.config import (
    MAINTENANCE_INTERVAL,
    PROGRESS_INTERVAL,
    OptionsError,
    effective_options,
    get_settings,
)
from standbild.db import repo
from standbild.pipeline import Result, run_pipeline
from standbild.pipeline.fetch import FetchError
from standbild.youtube import extract_id, video_info, watch_url

log = logging.getLogger(__name__)
Pipeline = Callable[..., Result]


def view(job: repo.Job) -> dict[str, Any]:
    """Status eines Auftrags – dasselbe Format in REST und MCP (11.2)."""
    return {
        "job_id": str(job["id"]),
        "status": job["status"],
        "stage": job["stage"],
        "progress": round(job["progress"], 3),
        "title": job["title"],
        "created_at": job["created_at"].isoformat(),
        "finished_at": job["finished_at"].isoformat() if job["finished_at"] else None,
        "error": job["error"],
        "screenshot_count": job["screenshot_count"],
        "package_size": job["package_size"],
    }


def package_path(job: repo.Job) -> Path | None:
    """Pfad des ZIPs, falls der Auftrag fertig ist und die Datei noch im Volume liegt."""
    if job["status"] != "done" or not job["package_file"]:
        return None
    path = get_settings().packages_dir / job["package_file"]
    return path if path.is_file() else None


def submit(url: str, options: dict[str, Any] | None = None, force: bool = False) -> dict[str, Any]:
    """Auftrag anlegen oder ein passendes Ergebnis wiederverwenden (11.1). Wirft ValueError/OptionsError."""
    youtube_id = extract_id(url)
    opts = effective_options(options)
    opts["whisper_model"] = opts["whisper_model"] or models.default_model()
    if opts["whisper_model"] not in models.installed():
        raise OptionsError(
            {"whisper_model": f"Modell nicht installiert – erlaubt: {', '.join(models.installed())}"}
        )
    job = None if force else repo.latest_done(youtube_id, opts)
    reused = job is not None and package_path(job) is not None
    if not reused or job is None:
        previous = repo.latest(youtube_id)
        title = (previous or {}).get("title") or (video_info(youtube_id) or {}).get("title")
        job, created = repo.create(youtube_id, title, opts)
        reused = not created  # für das Video wartet oder läuft schon ein Auftrag
    return {**view(job), "reused": reused, "youtube_id": youtube_id, "options": job["options"]}


def process(job: repo.Job, pipeline: Pipeline = run_pipeline) -> None:
    """Führt einen geholten Auftrag aus und schreibt Fortschritt, Ergebnis oder Fehler in die Datenbank."""
    s = get_settings()
    state: dict[str, Any] = {"stage": "download", "progress": 0.0}
    finished = threading.Event()

    def beat() -> None:  # Heartbeat und Fortschritt in einem: alle 2 s ein UPDATE
        while not finished.wait(PROGRESS_INTERVAL):
            try:
                repo.beat(job["id"], state["stage"], state["progress"])
            except Exception:
                log.warning("Heartbeat für %s fehlgeschlagen", job["id"], exc_info=True)

    beater = threading.Thread(target=beat, daemon=True, name=f"beat-{job['id']}")
    beater.start()
    try:
        result = pipeline(
            watch_url(job["youtube_id"]),
            job["options"],
            workdir=s.tmp_dir / str(job["id"]),
            out_dir=s.packages_dir,
            model_dir=models.path(str(job["options"]["whisper_model"])),
            device=s.device,
            condition_on_previous_text=s.condition_on_previous_text,
            template_dir=s.template_dir,
            progress=lambda stage, p: state.update(stage=stage, progress=p),
            on_meta=lambda meta: repo.set_title(job["id"], meta.get("title")),
        )
        size = result.package.stat().st_size
        if repo.finish(job["id"], result.package.name, size, result.screenshots, result.title):
            for name in repo.prune(job["youtube_id"], s.keep_results):
                (s.packages_dir / name).unlink(missing_ok=True)
        else:  # der Auftrag wurde inzwischen neu vergeben (Heartbeat abgelaufen)
            result.package.unlink(missing_ok=True)
    except Exception as exc:
        log.exception("Auftrag %s fehlgeschlagen", job["id"])
        repo.fail(
            job["id"], str(exc) if isinstance(exc, FetchError) else f"{type(exc).__name__}: {exc}"[:300]
        )
    finally:
        finished.set()
        beater.join()


def _is_uuid(name: str) -> bool:
    try:
        uuid.UUID(name)
    except ValueError:
        return False
    return True


def maintain() -> None:
    """Regelmäßig: hängende Aufträge zurücksetzen, alte Fehlschläge löschen, verwaiste Arbeitsverzeichnisse räumen."""
    s = get_settings()
    for job in repo.requeue_stale():
        log.warning(
            "Auftrag %s ohne Lebenszeichen → %s (Start %d)", job["id"], job["status"], job["attempts"]
        )
    repo.prune_failed()
    dirs = [d for d in s.tmp_dir.iterdir() if _is_uuid(d.name)] if s.tmp_dir.is_dir() else []
    running = (
        repo.running_ids()
    )  # nach dem Auflisten lesen: ein Verzeichnis entsteht erst nach dem Holen des Auftrags
    for d in dirs:
        if d.name not in running:
            shutil.rmtree(d, ignore_errors=True)


class Workers:
    """Worker-Threads im serve-Prozess."""

    def __init__(self, pipeline: Pipeline = run_pipeline):
        self.pipeline = pipeline
        self.stop = threading.Event()
        self.current: set[Any] = set()
        self.threads: list[threading.Thread] = []

    def start(self) -> None:
        n = get_settings().workers
        self.threads = [
            threading.Thread(target=self._work, daemon=True, name=f"worker-{i}") for i in range(n)
        ]
        self.threads.append(threading.Thread(target=self._maintain, daemon=True, name="maintenance"))
        for t in self.threads:
            t.start()

    def alive(self) -> bool:
        """Für den Healthcheck: Kein Worker- oder Wartungs-Thread ist abgestürzt."""
        return all(t.is_alive() for t in self.threads)

    def _work(self) -> None:
        while not self.stop.is_set():
            try:
                job = repo.claim()
            except Exception:
                log.exception("Warteschlange nicht erreichbar")
                self.stop.wait(5.0)
                continue
            if job is None:
                self.stop.wait(1.0)
                continue
            self.current.add(job["id"])
            try:
                process(job, self.pipeline)
            finally:
                self.current.discard(job["id"])

    def _maintain(self) -> None:
        while True:
            try:
                maintain()
            except Exception:
                log.exception("Wartung fehlgeschlagen")
            if self.stop.wait(MAINTENANCE_INTERVAL):
                return

    def shutdown(self) -> None:
        """Beim geordneten Stopp laufende Aufträge sofort zurück in die Warteschlange (sonst erst nach 5 min)."""
        self.stop.set()
        for job_id in list(self.current):
            repo.release(job_id)
