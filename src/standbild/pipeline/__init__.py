"""Die Pipeline eines Auftrags (Abschnitt 3): Download → Transkription ∥ Bildwechsel → Zusammenführen → Ablegen."""

from __future__ import annotations

import json
import logging
import shutil
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from standbild.pipeline.detect import Detector, detect, hms
from standbild.pipeline.fetch import fetch
from standbild.pipeline.package import package_name, versions, write_files, write_zip
from standbild.pipeline.transcribe import from_vtt, transcribe


@dataclass
class Result:
    package: Path
    video_id: str
    title: str
    screenshots: int


def run_pipeline(
    source: str,
    opts: dict[str, Any],
    *,
    workdir: Path,
    out_dir: Path,
    model_dir: Path,
    device: str = "auto",
    condition_on_previous_text: bool = True,
    template_dir: Path | None = None,
    cookies: Path | None = None,
    progress: Callable[[str, float], None] = lambda stage, p: None,
    on_meta: Callable[[dict[str, Any]], None] = lambda meta: None,
) -> Result:
    """Verarbeitet ein Video zu einem ZIP in ``out_dir``. ``workdir`` wird angelegt und immer wieder gelöscht."""
    shutil.rmtree(workdir, ignore_errors=True)
    (workdir / "log").mkdir(parents=True)
    log = logging.getLogger(f"standbild.job.{workdir.name}")
    log.setLevel(logging.INFO)
    handler = logging.FileHandler(workdir / "log" / "processing.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)

    @contextmanager
    def stage(name: str) -> Iterator[None]:
        start = time.monotonic()
        log.info("Stufe %s: Start", name)
        yield
        log.info("Stufe %s: Ende nach %.1f s", name, time.monotonic() - start)

    try:
        log.info("Versionen: %s", json.dumps(versions()))
        log.info("Quelle: %s", source)
        log.info("Wirksame Einstellungen: %s", json.dumps(opts, ensure_ascii=False))

        progress("download", 0.0)
        with stage("download"):
            meta, video, audio, subs = fetch(
                source, workdir, opts, cookies, lambda p: progress("download", p)
            )
        on_meta(meta)
        log.info(
            "Video: %s – %s, %d×%d", meta.get("title"), hms(meta["duration"]), meta["width"], meta["height"]
        )

        # Stufen 2 und 3 laufen parallel; gemeldet wird die jeweils langsamere.
        done = {"transcribe": 0.0, "detect": 0.0}

        def report(name: str, p: float) -> None:
            done[name] = p
            slowest = min(done, key=lambda k: done[k])
            progress(slowest, done[slowest])

        def run_detect() -> Detector:
            with stage("detect"):
                return detect(video, opts, workdir / "frames", lambda p: report("detect", p))

        def run_transcribe() -> dict[str, Any]:
            with stage("transcribe"):
                if opts["use_subtitles"] and subs:
                    log.info("Manuelle Untertitel (%s) statt Whisper", meta.get("subtitle_language"))
                    return {
                        **from_vtt(subs.read_text(encoding="utf-8"), meta.get("subtitle_language", "")),
                        "from_subtitles": True,
                    }
                if opts["use_subtitles"]:
                    log.warning("Keine manuellen Untertitel gefunden – Whisper wird verwendet")
                if audio is None:
                    log.warning("Keine Tonspur – das Transkript bleibt leer")
                    return {"language": "", "segments": [], "removed_repetitions": 0}
                if not (model_dir / "model.bin").is_file():
                    raise RuntimeError(f"Whisper-Modell nicht gefunden: {model_dir.name}")
                return transcribe(
                    audio,
                    model_dir,
                    opts["lang"],
                    device,
                    lambda p: report("transcribe", p),
                    hint=meta.get("title") if meta.get("url") else None,  # nur echte Titel, keine Dateinamen
                    condition_on_previous_text=condition_on_previous_text,
                )

        report("transcribe", 0.0)
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(run_detect)
            transcript = run_transcribe()
            report("transcribe", 1.0)
            det = future.result()

        images = len({s.file for s in det.shots})
        log.info("Erkannte Sprache: %s", transcript["language"] or "–")
        log.info("Screenshots: %d, Duplikate: %d", images, len(det.shots) - images)
        if transcript["removed_repetitions"]:
            log.warning("Entfernte Whisper-Halluzinationen: %d Segmente", transcript["removed_repetitions"])
        if not det.shots:
            log.warning("Kein Screenshot gespeichert (Video durchgehend schwarz oder einfarbig?)")

        progress("assemble", 0.0)
        now = datetime.now().astimezone()
        with stage("assemble"):
            write_files(workdir, meta, transcript, det, opts, now, template_dir)
        progress("store", 0.0)
        name = package_name(str(meta["id"]), now, out_dir)
        log.info("Stufe store: Paket %s", name)
        handler.close()  # das Protokoll kommt mit ins ZIP
        package = write_zip(workdir, out_dir, name)
        return Result(package, str(meta["id"]), str(meta.get("title") or meta["id"]), images)
    finally:
        log.removeHandler(handler)
        handler.close()
        shutil.rmtree(workdir, ignore_errors=True)
