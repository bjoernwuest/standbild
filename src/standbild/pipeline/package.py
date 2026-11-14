"""Stufe 4b/5: transcript.md, AGENTS.md, Protokolldateien und das ZIP-Paket (Abschnitt 8.3–8.5)."""

from __future__ import annotations

import csv
import json
import os
import zipfile
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from standbild.pipeline.align import build_body
from standbild.pipeline.detect import Detector, hms, shots_json
from standbild.youtube import _ID

BUILTIN_TEMPLATES = Path(__file__).parent.parent / "templates"


def versions() -> dict[str, str]:
    return {
        p: version(p)
        for p in ("standbild", "yt-dlp", "faster-whisper", "ctranslate2", "opencv-python-headless")
    }


def write_files(
    workdir: Path,
    meta: dict[str, Any],
    transcript: dict[str, Any],
    det: Detector,
    opts: dict[str, Any],
    generated: datetime,
    template_dir: Path | None = None,
) -> None:
    """Schreibt alle Textdateien des Pakets nach ``workdir`` (frames/ und processing.log liegen dort schon)."""
    images = len({s.file for s in det.shots})
    is_youtube = "youtube.com/" in str(meta.get("url", "")) and bool(_ID.fullmatch(str(meta["id"])))
    from_subtitles = bool(transcript.get("from_subtitles"))
    source = "manuelle Untertitel" if from_subtitles else f"whisper {opts['whisper_model']}"
    title = meta.get("title") or meta["id"]
    head = {
        "title": title,
        "source": meta.get("url") or meta.get("file"),
        "channel": meta.get("channel"),
        "duration": hms(meta["duration"]),
        "language": transcript["language"],
        "screenshots": images,
        "generated": generated.isoformat(timespec="seconds"),
        "tool": f"standbild {version('standbild')} ({source}, profile {opts['profile']})",
    }
    # JSON-Strings sind gültiges YAML; URL, Zahlen und Zeitstempel bleiben wie im Beispiel der Spezifikation unquotiert.
    plain = ("source", "language", "screenshots", "generated", "tool")
    front = "\n".join(
        f"{k}: {v if k in plain else json.dumps(v, ensure_ascii=False)}"
        for k, v in head.items()
        if v is not None
    )
    body = build_body(
        transcript["segments"],
        det.shots,
        timestamps=opts["timestamps"],
        youtube_id=meta["id"] if is_youtube else None,
    )
    (workdir / "transcript.md").write_text(f"---\n{front}\n---\n\n# {title}\n\n{body}", encoding="utf-8")

    env = Environment(
        loader=FileSystemLoader([*([template_dir] if template_dir else []), BUILTIN_TEMPLATES]),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    agents = env.get_template("AGENTS.md.j2").render(
        **{**head, "url": head["source"], "chapters": meta.get("chapters"), "from_subtitles": from_subtitles}
    )
    (workdir / "AGENTS.md").write_text(agents, encoding="utf-8")

    full_meta = {
        **meta,
        "language": transcript["language"],
        "screenshots": images,
        "duplicates": len(det.shots) - images,
        "transcript_source": "subtitles" if from_subtitles else "whisper",
        "whisper_model": None if from_subtitles else opts["whisper_model"],
        "generated": head["generated"],
        "options": opts,
        "versions": versions(),
    }
    (workdir / "meta.json").write_text(json.dumps(full_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    log = workdir / "log"
    log.mkdir(exist_ok=True)
    (log / "frames.json").write_text(json.dumps(shots_json(det.shots), indent=1), encoding="utf-8")
    with (log / "detection_log.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["t", "global_ratio", "largest_region", "motion", "blank", "state", "action"]
        )
        writer.writeheader()
        writer.writerows(det.rows)


def package_name(video_id: str, when: datetime, out_dir: Path) -> str:
    """``<id>_<yyyymmdd-hhmm>.zip``; mit Sekunden, falls es den Namen in derselben Minute schon gibt."""
    name = f"{video_id}_{when:%Y%m%d-%H%M}.zip"
    return name if not (out_dir / name).exists() else f"{video_id}_{when:%Y%m%d-%H%M%S}.zip"


def write_zip(workdir: Path, out_dir: Path, name: str) -> Path:
    """Packt das Paket unter temporärem Namen und benennt es atomar um (9.2)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    target, part = out_dir / name, out_dir / (name + ".part")
    members = ["AGENTS.md", "transcript.md", "meta.json"]
    members += sorted(f"frames/{p.name}" for p in (workdir / "frames").glob("*.png"))
    members += ["log/processing.log", "log/frames.json", "log/detection_log.csv"]
    with zipfile.ZipFile(part, "w") as z:
        for m in members:
            # PNG ist schon komprimiert: nur speichern
            z.write(workdir / m, m, zipfile.ZIP_STORED if m.endswith(".png") else zipfile.ZIP_DEFLATED)
    os.replace(part, target)
    return target
