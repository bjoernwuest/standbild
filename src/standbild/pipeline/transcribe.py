"""Stufe 2: Transkription mit faster-whisper, lokal (Abschnitt 6)."""

from __future__ import annotations

import html
import itertools
import re
import threading
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from standbild.config import MAX_REPEATS, MAX_SPEECH_RATE, TAIL_MARGIN

_local = threading.local()  # jeder Worker-Thread hält sein zuletzt benutztes Modell


def resolve_device(device: str) -> tuple[str, str]:
    """(Rechenwerk, compute_type): cuda/float16 mit GPU, sonst cpu/int8."""
    if device == "auto":
        import ctranslate2

        device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    return device, "float16" if device == "cuda" else "int8"


def load_model(model_dir: Path, device: str) -> Any:
    key = (str(model_dir), device)
    if getattr(_local, "key", None) != key:
        from faster_whisper import WhisperModel

        _local.model = None  # altes Modell freigeben, bevor das neue geladen wird
        dev, compute_type = resolve_device(device)
        _local.model = WhisperModel(str(model_dir), device=dev, compute_type=compute_type)
        _local.key = key
    return _local.model


def drop_repeats(segments: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Entfernt Segmente, die sich mehr als MAX_REPEATS-mal wörtlich wiederholen (Halluzinationen)."""
    kept: list[dict[str, Any]] = []
    removed = 0
    for _, group in itertools.groupby(segments, key=lambda s: s["text"].strip().lower()):
        run = list(group)
        if len(run) > MAX_REPEATS:
            removed += len(run)
        else:
            kept += run
    return [{"id": i, **s} for i, s in enumerate(kept)], removed


def load_wav(path: Path) -> NDArray[np.float32]:
    """Liest audio.wav (16 kHz, mono, 16 Bit – so erzeugt es fetch) ohne den Audio-Dekoder von faster-whisper,
    dessen PyAV-Aufruf mit neueren PyAV-Versionen bricht."""
    with wave.open(str(path), "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
            raise ValueError(f"{path.name}: erwartet 16 kHz, mono, 16 Bit")
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0


def transcribe(
    audio: Path,
    model_dir: Path,
    lang: str = "auto",
    device: str = "auto",
    progress: Callable[[float], None] = lambda p: None,
    hint: str | None = None,
    condition_on_previous_text: bool = True,
) -> dict[str, Any]:
    """``hint`` (der Videotitel) geht als Hotwords in jedes 30-s-Fenster: richtige Schreibweise von Produktnamen
    und durchgehend Satzzeichen. Begründung der Einstellungen: DECISIONS.md."""
    model = load_model(model_dir, device)
    samples = load_wav(audio)
    duration = len(samples) / 16000
    parts, info = model.transcribe(
        samples,
        language=None if lang == "auto" else lang,
        word_timestamps=True,
        vad_filter=True,
        condition_on_previous_text=condition_on_previous_text,
        hotwords=hint or None,
    )
    segments = []
    invented = 0
    for s in parts:  # Generator: die Transkription läuft während der Iteration
        # Erfundener Text verrät sich durch seine Zeitstempel: Er "beginnt" erst am Audioende oder ist
        # schneller, als ein Mensch sprechen kann (gemessen: echte Segmente ≤ 32 Zeichen/s, erfundene ≥ 58).
        rate = len(s.text.strip()) / max(s.end - s.start, 1e-3)
        if s.start >= duration - TAIL_MARGIN or rate > MAX_SPEECH_RATE:
            invented += 1
            continue
        words = [{"start": round(w.start, 3), "end": round(w.end, 3), "word": w.word} for w in s.words or []]
        segments.append(
            {"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip(), "words": words}
        )
        progress(min(1.0, s.end / info.duration) if info.duration else 0.0)
    segments, removed = drop_repeats(segments)
    return {"language": info.language, "segments": segments, "removed_repetitions": removed + invented}


_CUE = re.compile(r"(?:(\d+):)?(\d\d):(\d\d)\.(\d{3}) --> (?:(\d+):)?(\d\d):(\d\d)\.(\d{3})")


def from_vtt(text: str, language: str) -> dict[str, Any]:
    """Manuelle Untertitel ins Format von transcript.json; ohne Wort-Zeitstempel."""
    segments: list[dict[str, Any]] = []
    for block in re.split(r"\n\s*\n", text.replace("\r", "")):
        lines = block.strip().splitlines()
        for i, line in enumerate(lines):
            if m := _CUE.search(line):
                h1, m1, s1, ms1, h2, m2, s2, ms2 = (int(x or 0) for x in m.groups())
                body = html.unescape(
                    " ".join(re.sub(r"<[^>]+>", "", ln).strip() for ln in lines[i + 1 :])
                ).strip()
                if body:
                    start, end = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000, h2 * 3600 + m2 * 60 + s2 + ms2 / 1000
                    segments.append({"start": start, "end": end, "text": body, "words": []})
                break
    return {
        "language": language,
        "segments": [{"id": i, **s} for i, s in enumerate(segments)],
        "removed_repetitions": 0,
    }
