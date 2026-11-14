"""Stufe 4a: Einfügeregel (Abschnitt 8.1/8.2) – Markierungen an Satzgrenzen in den Fließtext setzen."""

from __future__ import annotations

import math
import re
from typing import Any

from standbild.config import ALIGN_TOLERANCE, PARAGRAPH_PAUSE, SENTENCE_PAUSE
from standbild.pipeline.detect import Shot, hms

_SENTENCE_END = re.compile(r"[.?!:][\"'”“’)\]]*$")
_CLAUSE_END = re.compile(r"[,;–—]$")


def marker(shot: Shot) -> str:
    return f">>>>> ![frame_{shot.n:04d} · {hms(shot.t)}](frames/{shot.file})"


def build_body(
    segments: list[dict[str, Any]],
    shots: list[Shot],
    *,
    timestamps: bool = True,
    youtube_id: str | None = None,
) -> str:
    """Transkript-Fließtext mit Markierungen. Position k bedeutet "vor Einheit k"."""
    # Einheit = Wort; ohne Wort-Zeitstempel (Untertitel) ein ganzes Segment, das immer einen Satz beendet.
    units: list[tuple[float, float, str, bool]] = []
    for seg in segments:
        if seg.get("words"):
            units += [
                (w["start"], w["end"], w["word"], bool(_SENTENCE_END.search(w["word"].strip())))
                for w in seg["words"]
            ]
        elif seg["text"].strip():
            units.append((seg["start"], seg["end"], " " + seg["text"].strip(), True))
    n = len(units)
    ends = [-math.inf] + [u[1] for u in units]  # Ende der Einheit vor Position k
    starts = [u[0] for u in units] + [math.inf]  # Beginn der Einheit an Position k

    def distance(k: int, t: float) -> float:
        return max(ends[k] - t, t - starts[k], 0.0)

    sentence = [
        k for k in range(n + 1) if k in (0, n) or units[k - 1][3] or starts[k] - ends[k] >= SENTENCE_PAUSE
    ]
    clause = [k for k in range(1, n) if _CLAUSE_END.search(units[k - 1][2].strip())]
    at: dict[int, list[Shot]] = {}
    for shot in sorted(shots, key=lambda s: s.t):
        # Nächste Satzgrenze; bei sehr langen Sätzen lieber an einem Komma trennen als mitten im Satzteil,
        # sonst an der nächsten Wortgrenze.
        for candidates in (sentence, clause, range(n + 1)):
            k = min(candidates, key=lambda k: distance(k, shot.t_detect), default=0)
            if distance(k, shot.t_detect) <= ALIGN_TOLERANCE:
                break
        at.setdefault(k, []).append(shot)

    blocks: list[str] = []
    words: list[str] = []
    para_start = 0.0

    def flush() -> None:
        if words:
            text = " ".join("".join(words).split())
            link = (
                f"(https://www.youtube.com/watch?v={youtube_id}&t={int(para_start)}s)" if youtube_id else ""
            )
            blocks.append(f"[{hms(para_start)}]{link} {text}" if timestamps else text)
            words.clear()

    for k in range(n + 1):
        if k in at or (0 < k < n and starts[k] - ends[k] >= PARAGRAPH_PAUSE):
            flush()
        blocks += [marker(s) for s in at.get(k, [])]
        if k < n:
            if not words:
                para_start = starts[k]
            words.append(units[k][2])
    flush()
    return "\n\n".join(blocks) + "\n"
