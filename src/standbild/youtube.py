"""YouTube: URL-Normalisierung auf die Video-ID (9.1) und Metadaten ohne Download."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any
from urllib.parse import parse_qs, urlparse

_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "www.youtube-nocookie.com"}
_ID = re.compile(r"[A-Za-z0-9_-]{11}")


def extract_id(url: str) -> str:
    """Liefert die 11-stellige Video-ID oder wirft ValueError mit einer klaren Meldung."""
    url = url.strip()
    u = urlparse(url if "://" in url else "https://" + url)
    host = (u.hostname or "").lower()
    parts = [p for p in u.path.split("/") if p]
    vid: str | None = None
    if u.scheme not in ("http", "https"):
        pass
    elif host == "youtu.be":
        vid = parts[0] if parts else None
    elif host in _HOSTS:
        if parts == ["watch"]:
            vid = parse_qs(u.query).get("v", [None])[0]
        elif len(parts) == 2 and parts[0] in ("shorts", "embed", "live"):
            vid = parts[1]
    if not vid or not _ID.fullmatch(vid):
        raise ValueError("Kein gültiger YouTube-Link (erwartet z. B. https://www.youtube.com/watch?v=…)")
    return vid


def watch_url(youtube_id: str) -> str:
    return f"https://www.youtube.com/watch?v={youtube_id}"


def ydl_options(**extra: Any) -> dict[str, Any]:
    """Gemeinsame yt-dlp-Optionen. YouTube braucht eine JS-Laufzeit; im Image ist das deno."""
    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "js_runtimes": {"deno": {}, "node": {}},
        **extra,
    }


@lru_cache(maxsize=256)
def _video_info(youtube_id: str) -> dict[str, Any]:
    import yt_dlp

    with yt_dlp.YoutubeDL(ydl_options(skip_download=True)) as ydl:
        info = ydl.extract_info(watch_url(youtube_id), download=False, process=False)
    return {k: info.get(k) for k in ("title", "channel", "duration")}


def video_info(youtube_id: str) -> dict[str, Any] | None:
    """Titel für die Karte „Unbekanntes Video“ (ohne Download); None, wenn nicht abrufbar. Fehler werden nicht gemerkt."""
    try:
        return _video_info(youtube_id)
    except Exception:
        return None
