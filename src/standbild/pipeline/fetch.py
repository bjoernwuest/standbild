"""Stufe 1: Eingabe und Download nach /tmp (Abschnitt 5)."""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from standbild.config import DOWNLOAD_ATTEMPTS, DOWNLOAD_RETRY_PAUSE
from standbild.pipeline.detect import probe
from standbild.youtube import ydl_options


class FetchError(RuntimeError):
    """Download oder Eingabe fehlgeschlagen; die Meldung ist für Nutzer gedacht (CLI: Exit-Code 2)."""


# Typische Meldungen, wenn YouTube etwas geändert hat und yt-dlp nachziehen muss (12.5)
_OUTDATED = (
    "unable to extract",
    "nsig",
    "signature",
    "requested format is not available",
    "http error 403",
    "player",
)
_KNOWN = (
    (
        ("confirm your age", "age-restricted", "age restricted"),
        "Video ist altersbeschränkt (cookies.txt nötig: --cookies)",
    ),
    (("private video",), "Video ist privat"),
    (("not a bot", "sign in"), "YouTube verlangt eine Anmeldung (cookies.txt nötig: --cookies)"),
    (("video unavailable", "has been removed", "is not available"), "Video ist nicht verfügbar"),
    (("name resolution", "timed out", "connection", "network"), "Quelle nicht erreichbar (Netzwerk)"),
)


def explain(exc: Exception) -> str:
    text = str(exc).removeprefix("ERROR: ")
    low = text.lower()
    msg = next((m for keys, m in _KNOWN if any(k in low for k in keys)), None)
    if msg is None:
        msg = "Download fehlgeschlagen: " + text.splitlines()[0][:200]
        if any(k in low for k in _OUTDATED):
            msg += " – yt-dlp evtl. veraltet – Image neu bauen"
    return msg


def fetch(
    source: str,
    workdir: Path,
    opts: dict[str, Any],
    cookies: Path | None = None,
    progress: Callable[[float], None] = lambda p: None,
) -> tuple[dict[str, Any], Path, Path | None, Path | None]:
    """Liefert (meta, Video, audio.wav oder None, subs.vtt oder None)."""
    subs: Path | None = None
    if "://" not in source:
        video = Path(source)
        if not video.is_file():
            raise FetchError(f"Datei nicht gefunden: {source}")
        meta: dict[str, Any] = {
            "id": re.sub(r"[^A-Za-z0-9_-]+", "-", video.stem)[:40] or "video",
            "title": video.stem,
            "file": video.name,
        }
    else:
        import yt_dlp

        def hook(d: dict[str, Any]) -> None:
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if d.get("status") == "downloading" and total:
                progress(min(1.0, d.get("downloaded_bytes", 0) / total))

        h = opts["max_height"]
        lang = opts["lang"]
        ydl_opts = ydl_options(
            format=f"bv*[height<={h}]+ba/b[height<={h}]",
            merge_output_format="mp4",
            outtmpl=str(workdir / "source.%(ext)s"),
            noplaylist=True,
            progress_hooks=[hook],
            cookiefile=str(cookies) if cookies else None,
            # Nur manuell erstellte Untertitel; automatisch erzeugte werden nie verwendet.
            writesubtitles=opts["use_subtitles"],
            writeautomaticsub=False,
            subtitlesformat="vtt",
            subtitleslangs=["all", "-live_chat"] if lang == "auto" else [lang, f"{lang}-.*"],
        )
        attempt = 0
        while True:
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(source, download=True)
                break
            except Exception as exc:  # yt-dlp wirft je nach Ursache sehr unterschiedliche Typen
                if ydl_opts["writesubtitles"] and "subtitles" in str(exc):
                    ydl_opts["writesubtitles"] = (
                        False  # Untertitel nicht abrufbar (oft HTTP 429): Whisper übernimmt
                    )
                    continue
                attempt += 1
                # YouTube antwortet gelegentlich mit 403/429/5xx; ein neuer Anlauf holt frisch signierte Adressen.
                if attempt == DOWNLOAD_ATTEMPTS or not re.search(r"HTTP Error (403|429|5\d\d)", str(exc)):
                    raise FetchError(explain(exc)) from exc
                time.sleep(DOWNLOAD_RETRY_PAUSE)
        video = workdir / "source.mp4"
        if not video.is_file():
            raise FetchError("Download fehlgeschlagen: keine Videodatei erhalten")
        meta = {
            "id": info.get("id"),
            "title": info.get("title"),
            "channel": info.get("channel") or info.get("uploader"),
            "upload_date": info.get("upload_date"),
            "url": info.get("webpage_url") or source,
            "chapters": [
                {"start": c["start_time"], "title": c.get("title")} for c in info.get("chapters") or []
            ],
            "description": info.get("description"),
        }
        want = lang if lang != "auto" else (info.get("language") or "")[:2]
        found = sorted(workdir.glob("source.*.vtt"))
        match = [p for p in found if want and p.name.split(".")[1].startswith(want)] or (
            found if len(found) == 1 else []
        )
        if match:
            subs = match[0].rename(workdir / "subs.vtt")
            meta["subtitle_language"] = match[0].name.split(".")[1]
    try:
        meta.update(probe(video))
    except Exception as exc:
        raise FetchError(f"Keine lesbare Videodatei: {video.name}") from exc

    audio: Path | None = workdir / "audio.wav"
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(video),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(audio),
    ]
    if subprocess.run(cmd, capture_output=True).returncode != 0:
        audio = None  # keine Tonspur (stumme Demo): das Transkript bleibt leer
    return meta, video, audio, subs
