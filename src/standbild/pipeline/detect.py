"""Stufe 3: Erkennung von Bildwechseln (Abschnitt 7) – reine Messfunktionen + Zustandsautomat."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, NamedTuple, cast

import cv2
import imagehash
import numpy as np
from numpy.typing import NDArray
from PIL import Image
from skimage.metrics import structural_similarity

from standbild.config import BLANK_MEAN, BLANK_STD, DUP_HAMMING, DUP_SSIM, MOTION_QUIET, RATE_WINDOW

Gray = NDArray[np.uint8]
_K3 = np.ones((3, 3), np.uint8)
_K5 = np.ones((5, 5), np.uint8)
QUIET, CHANGING = "RUHIG", "WECHSELT"


# --- reine Berechnung ---------------------------------------------------------------------------
class Metrics(NamedTuple):
    global_ratio: float
    largest_region: float


def preprocess(frame: NDArray[np.uint8], width: int, masks: list[list[float]]) -> Gray:
    """Vergleichsbild: verkleinert, Graustufen, weichgezeichnet, Masken auf 0 (7.2)."""
    h, w = frame.shape[:2]
    width = min(width, w)
    small = cv2.resize(frame, (width, max(1, round(h * width / w))), interpolation=cv2.INTER_AREA)
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (3, 3), 0)
    gh, gw = gray.shape
    for x, y, mw, mh in masks:
        gray[round(y * gh) : round((y + mh) * gh), round(x * gw) : round((x + mw) * gw)] = 0
    return cast(Gray, gray)


def unmasked(shape: tuple[int, ...], masks: list[list[float]]) -> NDArray[np.bool_]:
    valid = np.ones(shape, np.bool_)
    gh, gw = shape
    for x, y, mw, mh in masks:
        valid[round(y * gh) : round((y + mh) * gh), round(x * gw) : round((x + mw) * gw)] = False
    return valid


def is_blank(gray: Gray, valid: NDArray[np.bool_] | None = None) -> bool:
    """Fast schwarz oder einfarbig: Überblendung, wird nie gespeichert (7.2)."""
    px = gray if valid is None else gray[valid]
    return px.size == 0 or float(px.mean()) < BLANK_MEAN or float(px.std()) < BLANK_STD


def measure(gray: Gray, ref: Gray, pixel_noise: int, n_valid: int | None = None) -> Metrics:
    """global_ratio und largest_region zwischen zwei Vergleichsbildern (7.3)."""
    diff = (cv2.absdiff(gray, ref) > pixel_noise).astype(np.uint8)
    mask = cv2.morphologyEx(diff, cv2.MORPH_OPEN, _K3)
    changed = int(cv2.countNonZero(mask))
    if not changed:
        return Metrics(0.0, 0.0)
    _, _, stats, _ = cv2.connectedComponentsWithStats(cv2.dilate(mask, _K5))
    return Metrics(changed / (n_valid or mask.size), int(stats[1:, cv2.CC_STAT_AREA].max()) / mask.size)


# --- Zustandsautomat ----------------------------------------------------------------------------
@dataclass
class Shot:
    """Ein Screenshot-Ereignis. Bei einem Duplikat zeigen ``n`` und ``file`` auf das frühere Bild."""

    n: int
    file: str
    t: float
    t_detect: float
    global_ratio: float
    largest_region: float
    reason: str  # first | global | local | forced
    duplicate_of: str | None = None


def hms(t: float, sep: str = ":") -> str:
    s = int(t)
    return f"{s // 3600:02d}{sep}{s // 60 % 60:02d}{sep}{s % 60:02d}"


class Detector:
    """Bekommt die analysierten Frames der Reihe nach und entscheidet, welche gespeichert werden (7.4–7.6)."""

    def __init__(self, opts: dict[str, Any], save: Callable[[str, NDArray[np.uint8]], None]):
        self.o = opts
        self.save = save
        self.shots: list[Shot] = []
        self.rows: list[dict[str, Any]] = []  # detection_log.csv
        # Bis zum ersten Screenshot gilt das Bild als "im Wechsel": gespeichert wird das erste ruhige Frame.
        self.state = CHANGING
        self.t_detect: float | None = None
        self.quiet_since = 0.0
        self.ref: Gray | None = None
        self.prev: Gray | None = None
        self.valid: NDArray[np.bool_] | None = None
        self.n_valid = 0
        self._kept: list[tuple[Shot, Gray, Any]] = []  # eigenständige Bilder für die Duplikatprüfung

    def _trigger(self, m: Metrics) -> str | None:
        """Auslöseregel (7.4)."""
        if m.global_ratio >= self.o["threshold"]:
            return "global"
        if m.largest_region >= self.o["min_local_change"]:
            return "local"
        return None

    def _may_save(self, t: float) -> bool:
        """min_gap und max_per_minute (7.5). Solange gesperrt, wartet der Automat; gespeichert wird dann
        der letzte Stand – "nur der jeweils letzte Kandidat"."""
        if not self.shots:
            return True
        recent = sum(1 for s in self.shots if t - s.t < RATE_WINDOW)
        return bool(t - self.shots[-1].t >= self.o["min_gap"] and recent < self.o["max_per_minute"])

    def _store(self, t: float, frame: NDArray[np.uint8], gray: Gray, m: Metrics, reason: str) -> Shot:
        phash = imagehash.phash(Image.fromarray(gray))
        dup = next(
            (
                s
                for s, g, h in self._kept
                # pHash + SSIM wie in 7.6; zusätzlich darf die Auslöseregel nicht greifen, sonst würde eine
                # kleine lokale Änderung (neue Codezeile) als Duplikat verschluckt (siehe DECISIONS.md).
                if phash - h <= DUP_HAMMING
                and self._trigger(measure(gray, g, self.o["pixel_noise"], self.n_valid)) is None
                and structural_similarity(gray, g, data_range=255) >= DUP_SSIM
            ),
            None,
        )
        assert self.t_detect is not None
        shot = Shot(
            len(self._kept) + 1,
            "",
            round(t, 3),
            round(self.t_detect, 3),
            round(m[0], 5),
            round(m[1], 5),
            reason,
        )
        if dup:
            shot.n, shot.file, shot.duplicate_of = dup.n, dup.file, dup.file
        else:
            shot.file = f"frame_{shot.n:04d}_{hms(t, '-')}.png"
            self.save(shot.file, frame)
            self._kept.append((shot, gray, phash))
        self.shots.append(shot)
        self.ref = gray
        return shot

    def feed(self, t: float, frame: NDArray[np.uint8]) -> Shot | None:
        o = self.o
        gray = preprocess(frame, o["analysis_width"], o["masks"])
        if self.valid is None:
            self.valid = unmasked(gray.shape, o["masks"])
            self.n_valid = int(self.valid.sum())
        blank = is_blank(gray, self.valid)
        motion = (
            measure(gray, self.prev, o["pixel_noise"], self.n_valid).global_ratio
            if self.prev is not None
            else 0.0
        )
        self.prev = gray
        if blank or motion >= MOTION_QUIET:
            self.quiet_since = t  # mit diesem Frame beginnt frühestens eine neue Ruhephase
        m = (
            measure(gray, self.ref, o["pixel_noise"], self.n_valid)
            if self.ref is not None
            else Metrics(1.0, 1.0)
        )
        reason = "first" if self.ref is None else self._trigger(m)
        action = ""
        shot: Shot | None = None

        if self.state == QUIET and reason:
            self.state, self.t_detect, action = CHANGING, t, "detect"
        if self.state == CHANGING:
            if self.t_detect is None:
                self.t_detect = t
            stable = t - self.quiet_since >= o["stable_time"]
            overdue = t - self.t_detect > o["max_wait"]
            if blank:
                if overdue:
                    self.t_detect = t  # Schwarzbild wird nie gespeichert, auch nicht erzwungen
            elif reason is None:
                if stable:
                    self.state, action = QUIET, "discard"  # z. B. Tooltip kurz eingeblendet
                else:
                    self.t_detect = t  # Bild entspricht gerade der Referenz: nichts ausstehend
            elif (stable or overdue) and self._may_save(t):
                shot = self._store(t, frame, gray, m, reason if stable or reason == "first" else "forced")
                action = ("duplicate:" if shot.duplicate_of else "save:") + shot.reason
                if stable:
                    self.state = QUIET
                else:
                    self.t_detect = t  # weiter in Bewegung (Scrollen, Videoeinspielung)
            elif stable or overdue:
                action = "wait"  # min_gap oder max_per_minute sperren noch
        self.rows.append(
            {
                "t": round(t, 3),
                "global_ratio": round(m.global_ratio, 5),
                "largest_region": round(m.largest_region, 5),
                "motion": round(motion, 5),
                "blank": int(blank),
                "state": self.state,
                "action": action,
            }
        )
        return shot


# --- Abtastung (7.1) ----------------------------------------------------------------------------
def probe(path: Path) -> dict[str, Any]:
    """Breite, Höhe und Dauer per ffprobe."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    data = json.loads(out)
    stream = data["streams"][0]
    return {
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "duration": float(data["format"]["duration"]),
    }


def read_frames(path: Path, interval: float) -> Iterator[tuple[float, NDArray[np.uint8]]]:
    """Dekodiert sequenziell und liefert alle ``interval`` Sekunden ein Frame in Originalauflösung."""
    info = probe(path)
    w, h = info["width"], info["height"]
    # shortcut: gedrehte Videos (Rotations-Metadaten) werden nicht unterstützt; bei Handyvideos -noautorotate ergänzen.
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-vf", f"fps={1 / interval:.6f}",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        stdout=subprocess.PIPE,
    )  # fmt: skip
    assert proc.stdout is not None
    try:
        i = 0
        while len(buf := proc.stdout.read(w * h * 3)) == w * h * 3:
            yield i * interval, np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            i += 1
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg konnte das Video nicht dekodieren (Exit-Code {proc.returncode})")
    finally:
        proc.kill()
        proc.wait()


def detect(
    video: Path, opts: dict[str, Any], frames_dir: Path, progress: Callable[[float], None] = lambda p: None
) -> Detector:
    """Führt die Erkennung über ein ganzes Video aus und schreibt die PNGs nach ``frames_dir``."""
    frames_dir.mkdir(parents=True, exist_ok=True)
    duration = probe(video)["duration"] or 1.0

    def save(name: str, frame: NDArray[np.uint8]) -> None:
        if not cv2.imwrite(str(frames_dir / name), frame, [cv2.IMWRITE_PNG_COMPRESSION, 6]):
            raise RuntimeError(f"Screenshot {name} konnte nicht geschrieben werden")

    det = Detector(opts, save)
    for t, frame in read_frames(video, opts["sample_interval"]):
        det.feed(t, frame)
        progress(min(1.0, t / duration))
    return det


def shots_json(shots: list[Shot]) -> list[dict[str, Any]]:
    return [{k: v for k, v in asdict(s).items() if k != "n"} for s in shots]
