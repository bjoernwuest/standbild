"""Whisper-Modelle im Volume /models (10.3): Seed, HuggingFace prüfen/laden, Standardmodell, entfernen.

Ablage ohne Datenbank: ein Verzeichnis pro Modell mit ``standbild-model.json``, Standardmodell in ``default.txt``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

from standbild.config import get_settings

log = logging.getLogger(__name__)
META = "standbild-model.json"
FILES = ["model.bin", "config.json", "tokenizer.json", "vocabulary.*", "preprocessor_config.json"]
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_SOURCE = re.compile(r"(?:https?://huggingface\.co/)?([\w.-]+/[\w.-]+?)(?:/tree/([^/?#]+))?/?")
_downloads: dict[str, dict[str, Any]] = {}  # laufende/fehlgeschlagene Downloads, nur im Speicher


class ModelError(ValueError):
    """Meldung für Nutzer (HTTP 400)."""


def root() -> Path:
    return get_settings().models_dir


def path(name: str) -> Path:
    return root() / name


def _size(directory: Path) -> int:
    return sum(f.stat().st_size for f in directory.rglob("*") if f.is_file())


def installed() -> list[str]:
    return sorted(d.name for d in root().iterdir() if (d / META).is_file()) if root().is_dir() else []


def default_model() -> str | None:
    f = root() / "default.txt"
    name = f.read_text().strip() if f.is_file() else ""
    return name if name in installed() else None


def set_default(name: str) -> None:
    if name not in installed():
        raise ModelError(f"Modell nicht installiert: {name}")
    tmp = root() / "default.txt.tmp"
    tmp.write_text(name)
    os.replace(tmp, root() / "default.txt")


def list_models() -> list[dict[str, Any]]:
    default = default_model()
    out: list[dict[str, Any]] = [
        {
            "name": n,
            **json.loads((path(n) / META).read_text()),
            "status": "ready",
            "progress": 1.0,
            "default": n == default,
        }
        for n in installed()
    ]
    for name, d in _downloads.items():
        progress = min(0.99, max(d["done"], d["net"]) / d["total"]) if d["total"] else 0.0
        out.append(
            {"name": name, "source": d["source"], "size": d["total"], "status": "error" if d["error"] else "downloading",
             "progress": progress, "error": d["error"], "default": False}
        )  # fmt: skip
    return out


def _install(name: str, fill: Callable[[Path], object], source: str, revision: str | None) -> None:
    """Füllt ``<name>.partial`` und benennt es nach Erfolg atomar um."""
    part = root() / f"{name}.partial"
    shutil.rmtree(part, ignore_errors=True)
    fill(part)
    shutil.rmtree(part / ".cache", ignore_errors=True)  # Verwaltungsdateien von huggingface_hub
    meta = {
        "source": source,
        "revision": revision,
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "size": _size(part),
    }
    (part / META).write_text(json.dumps(meta, indent=1))
    shutil.rmtree(path(name), ignore_errors=True)  # Rest ohne Metadatei, sonst scheitert das Umbenennen
    os.replace(part, path(name))


def seed() -> None:
    """Kopiert die Startausstattung des Images ins Volume, falls sie dort fehlt (Abschnitt 6)."""
    root().mkdir(parents=True, exist_ok=True)
    seed_dir = get_settings().seed_dir
    for src in sorted(d for d in seed_dir.iterdir() if d.is_dir()) if seed_dir.is_dir() else []:
        if src.name not in installed():
            log.info("Kopiere Modell %s aus dem Image nach %s", src.name, root())
            _install(src.name, partial(shutil.copytree, src), "Startausstattung des Images", None)
        if default_model() is None:
            set_default(src.name)
    if default_model() is None and installed():
        set_default(installed()[0])


def startup() -> None:
    root().mkdir(parents=True, exist_ok=True)
    for part in root().glob("*.partial"):  # abgebrochene Downloads
        shutil.rmtree(part, ignore_errors=True)
    seed()


def parse_source(url: str) -> tuple[str, str | None]:
    """HuggingFace-Link oder Repo-ID, optional mit ``/tree/<revision>`` → (Repo-ID, Revision)."""
    m = _SOURCE.fullmatch(url.strip())
    if not m:
        raise ModelError(
            "Kein HuggingFace-Link und keine Repo-ID (erwartet z. B. Systran/faster-whisper-medium)"
        )
    return m[1], m[2]


def check_repo(repo_id: str, revision: str | None) -> tuple[int, str | None]:
    """Prüft per HuggingFace-API ohne Download, ob das Repo ein CTranslate2-Modell ist → (Größe, Commit)."""
    from fnmatch import fnmatch

    from huggingface_hub import HfApi

    try:
        info = HfApi().model_info(repo_id, revision=revision, files_metadata=True)
    except Exception as exc:
        raise ModelError(f"Repo nicht gefunden oder nicht zugänglich: {repo_id}") from exc
    files = {s.rfilename: s.size or 0 for s in info.siblings or []}
    if not (
        {"model.bin", "config.json"} <= files.keys()
        and any(f == "tokenizer.json" or f.startswith("vocabulary.") for f in files)
    ):
        raise ModelError("Kein CTranslate2-Modell. Bitte eine faster-whisper-Variante wählen.")
    return sum(size for f, size in files.items() if any(fnmatch(f, p) for p in FILES)), info.sha


def add(url: str, name: str | None = None) -> str:
    """Startet den Download im Hintergrund und liefert den Modellnamen."""
    repo_id, revision = parse_source(url)
    name = name or repo_id.split("/")[1].removeprefix("faster-whisper-")
    if not _NAME.fullmatch(name):
        raise ModelError("Ungültiger Name (erlaubt: Buchstaben, Ziffern, Punkt, Binde- und Unterstrich)")
    if path(name).exists() or (name in _downloads and not _downloads[name]["error"]):
        raise ModelError(f"Name bereits vorhanden: {name}")
    total, sha = check_repo(repo_id, revision)
    _downloads[name] = {"source": repo_id, "total": total, "done": 0, "net": 0, "error": None}
    threading.Thread(
        target=_download, args=(name, repo_id, sha or revision), daemon=True, name=f"model-{name}"
    ).start()
    return name


def _byte_counter(state: dict[str, Any]) -> type:
    """tqdm-Ersatz für snapshot_download: zählt Bytes in ``state`` und gibt nichts aus."""
    from tqdm import tqdm

    class Counter(tqdm):  # type: ignore[misc]
        def __init__(self, *args: Any, **kw: Any) -> None:
            # huggingface_hub führt zwei Byte-Balken: empfangen ("Downloading bytes") und in die Datei geschrieben.
            # Große Dateien werden erst am Ende geschrieben, deshalb zählt für die Anzeige der größere Wert.
            self._key = (
                None if kw.get("unit") != "B" else "net" if "Downloading" in str(kw.get("desc")) else "done"
            )
            super().__init__(*args, **{**kw, "disable": True})

        def update(self, n: float | None = 1) -> None:
            if self._key:
                state[self._key] += n or 0
            super().update(n)

    return Counter


def _download(name: str, repo_id: str, revision: str | None) -> None:
    from huggingface_hub import snapshot_download

    counter = _byte_counter(_downloads[name])
    try:
        _install(
            name,
            lambda part: snapshot_download(
                repo_id, revision=revision, local_dir=part, allow_patterns=FILES, tqdm_class=counter
            ),
            repo_id,
            revision,
        )
        _downloads.pop(name, None)
    except Exception as exc:
        log.exception("Download von %s fehlgeschlagen", repo_id)
        shutil.rmtree(root() / f"{name}.partial", ignore_errors=True)
        _downloads[name]["error"] = f"Download fehlgeschlagen: {str(exc)[:200]}"


def remove(name: str, in_use: bool = False) -> None:
    if name in _downloads and _downloads[name]["error"]:
        del _downloads[name]  # fehlgeschlagenen Download aus der Liste nehmen
        return
    if name not in installed():
        raise ModelError(f"Modell nicht installiert: {name}")
    if name == default_model():
        raise ModelError("Das Standardmodell kann nicht entfernt werden.")
    if in_use:
        raise ModelError("Das Modell wird gerade von einem Auftrag verwendet.")
    shutil.rmtree(path(name))
