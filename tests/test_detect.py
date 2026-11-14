"""Erkennung von Bildwechseln mit synthetischen Videos (13.2, Szenarien a–g)."""

from collections.abc import Iterable
from pathlib import Path

import cv2
import numpy as np
import pytest

from standbild.config import RATE_WINDOW, effective_options
from standbild.pipeline.detect import Detector, Metrics, detect, is_blank, measure, preprocess

W, H, FPS = 640, 360, 10
COLORS = [(200, 180, 160), (60, 120, 200), (90, 200, 120), (180, 90, 180)]


def slide(i: int) -> np.ndarray:
    img = np.full((H, W, 3), COLORS[i % len(COLORS)], np.uint8)
    cv2.putText(img, f"Folie {i + 1}", (60, 120), cv2.FONT_HERSHEY_SIMPLEX, 2.0, (20, 20, 20), 4)
    cv2.rectangle(img, (60 + 90 * i, 180), (220 + 90 * i, 300), (250, 250, 250), -1)
    return img


def write_video(path: Path, frames: Iterable[np.ndarray]) -> Path:
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    assert out.isOpened()
    for f in frames:
        out.write(f)
    out.release()
    return path


def hold(img: np.ndarray, seconds: float) -> list[np.ndarray]:
    return [img] * int(seconds * FPS)


def run(tmp_path: Path, frames: Iterable[np.ndarray], **opts: object):
    video = write_video(tmp_path / "v.mp4", frames)
    return detect(video, effective_options(opts, env={}), tmp_path / "frames")


def test_a_hard_cuts(tmp_path: Path) -> None:
    det = run(tmp_path, [f for i in range(3) for f in hold(slide(i), 6)])
    assert [s.reason for s in det.shots] == ["first", "global", "global"]
    assert [s.file for s in det.shots] == [
        "frame_0001_00-00-01.png",
        "frame_0002_00-00-07.png",
        "frame_0003_00-00-13.png",
    ]
    # Originalauflösung, verlustfrei
    assert cv2.imread(str(tmp_path / "frames" / det.shots[0].file)).shape == (H, W, 3)
    assert [round(s.t_detect) for s in det.shots] == [0, 6, 12]


def test_b_crossfade_never_half_blended(tmp_path: Path) -> None:
    frames: list[np.ndarray] = hold(slide(0), 6)
    for i in (1, 2):
        frames += [cv2.addWeighted(slide(i - 1), 1 - a, slide(i), a, 0) for a in np.linspace(0, 1, FPS // 2)]
        frames += hold(slide(i), 6)
    det = run(tmp_path, frames)
    assert len(det.shots) == 3
    for i, s in enumerate(det.shots):
        saved = cv2.imread(str(tmp_path / "frames" / s.file))
        assert float(cv2.absdiff(saved, slide(i)).mean()) < 4, f"{s.file} ist überblendet"


def test_c_editor_lines_with_coding_profile(tmp_path: Path) -> None:
    img = np.full((H, W, 3), 30, np.uint8)
    cv2.rectangle(
        img, (0, 0), (W, 16), (70, 70, 70), -1
    )  # Titelleiste, sonst gälte der leere Editor als einfarbig
    frames = hold(img, 6)
    for n in range(5):
        img = img.copy()
        cv2.putText(
            img,
            f"value_{n} = compute(n={n})",
            (20, 40 + 28 * n),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (210, 210, 210),
            1,
        )
        frames += hold(img, 6)
    det = run(tmp_path, frames, profile="coding")
    assert [s.reason for s in det.shots] == ["first"] + ["local"] * 5
    assert not any(s.duplicate_of for s in det.shots)


def test_d_mouse_pointer_does_not_trigger(tmp_path: Path) -> None:
    frames = hold(slide(0), 2)
    for k in range(10 * FPS):
        img = slide(0).copy()
        x, y = 40 + 5 * k, 60 + 2 * k
        cv2.fillPoly(
            img, [np.array([[x, y], [x, y + 16], [x + 4, y + 12], [x + 11, y + 11]])], (255, 255, 255)
        )
        frames.append(img)
    det = run(tmp_path, frames + hold(slide(0), 2))
    assert len(det.shots) == 1


def test_e_webcam_noise_is_masked(tmp_path: Path) -> None:
    rng = np.random.default_rng(0)

    def frame(k: int) -> np.ndarray:  # Rauschen plus ein "Kopf", der sich nach 5 s zur Seite lehnt
        img = slide(0).copy()
        img[288:, 496:] = rng.integers(90, 130, (H - 288, W - 496, 3), np.uint8)
        cv2.circle(img, (540 if k < 5 * FPS else 600, 324), 22, (230, 230, 230), -1)
        return img

    det = run(tmp_path, [frame(k) for k in range(20 * FPS)], masks=[[0.75, 0.75, 0.25, 0.25]])
    assert len(det.shots) == 1
    unmasked = run(tmp_path, [frame(k) for k in range(20 * FPS)])
    assert len(unmasked.shots) > 1  # ohne Maske löst die Webcam aus


def test_f_return_to_first_slide_is_duplicate(tmp_path: Path) -> None:
    det = run(tmp_path, hold(slide(0), 6) + hold(slide(1), 6) + hold(slide(0), 6))
    assert [s.file for s in det.shots] == [
        "frame_0001_00-00-01.png",
        "frame_0002_00-00-07.png",
        "frame_0001_00-00-01.png",
    ]
    assert det.shots[2].duplicate_of == "frame_0001_00-00-01.png" and det.shots[2].n == 1
    assert det.shots[2].t > 12  # Zeit des Ereignisses, nicht des früheren Bildes
    assert sorted(p.name for p in (tmp_path / "frames").iterdir()) == [
        "frame_0001_00-00-01.png",
        "frame_0002_00-00-07.png",
    ]


def test_g_scrolling_respects_max_per_minute(tmp_path: Path) -> None:
    rng = np.random.default_rng(1)
    page = np.full((H * 12, W, 3), 245, np.uint8)
    for y in range(20, page.shape[0] - 20, 24):
        cv2.line(page, (20, y), (20 + int(rng.integers(150, 600)), y), (40, 40, 40), 6)
    frames = (
        hold(page[:H], 4)
        + [page[y : y + H] for y in range(0, 20 * FPS * 12, 12)]
        + hold(page[2400 : 2400 + H], 4)
    )
    det = run(tmp_path, frames, max_wait=2, min_gap=1, max_per_minute=3)
    times = [s.t for s in det.shots]
    assert "forced" in [s.reason for s in det.shots]
    assert all(sum(1 for u in times if 0 <= u - t < RATE_WINDOW) <= 3 for t in times)
    assert len(times) == 3
    assert "wait" in {r["action"] for r in det.rows}  # die Grenze hat tatsächlich gebremst
    assert len(det.rows) == pytest.approx(28 / 0.5, abs=2)  # eine Protokollzeile pro analysiertem Frame


# --- reine Messfunktionen und Zustandsautomat ohne Video --------------------------------------
def gray(img: np.ndarray) -> np.ndarray:
    return preprocess(img, 960, [])


def test_measure_and_blank() -> None:
    a = gray(slide(0))
    assert measure(a, a, 20) == Metrics(0.0, 0.0)
    b = slide(0).copy()
    cv2.rectangle(b, (300, 20), (360, 50), (0, 0, 0), -1)  # 60×30 px = 0,8 % des Bildes
    m = measure(gray(b), a, 20)
    assert 0.006 < m.global_ratio < 0.011 and m.largest_region >= m.global_ratio
    assert (
        is_blank(np.zeros((H, W), np.uint8)) and is_blank(np.full((H, W), 128, np.uint8)) and not is_blank(a)
    )
    masked = preprocess(b, 960, [[0.4, 0.0, 0.3, 0.2]])
    assert measure(masked, preprocess(slide(0), 960, [[0.4, 0.0, 0.3, 0.2]]), 20).global_ratio == 0


def feed(det: Detector, img: np.ndarray, start: float, seconds: float) -> float:
    t = start
    while t < start + seconds:
        det.feed(t, img)
        t += 0.5
    return t


def test_tooltip_is_discarded_and_black_never_saved() -> None:
    det = Detector(effective_options({}, env={}), lambda name, frame: None)
    t = feed(det, slide(0), 0, 3)
    tip = slide(0).copy()
    cv2.rectangle(tip, (300, 20), (420, 60), (0, 0, 0), -1)
    det.feed(t, tip)  # nur ein Frame lang sichtbar
    t = feed(det, slide(0), t + 0.5, 3)
    assert len(det.shots) == 1 and "discard" in [r["action"] for r in det.rows]
    t = feed(det, np.zeros((H, W, 3), np.uint8), t, 20)  # langes Schwarzbild: auch nicht erzwungen
    assert len(det.shots) == 1
    feed(det, slide(1), t, 3)
    assert [s.reason for s in det.shots] == ["first", "global"]


def test_min_gap_defers_instead_of_dropping() -> None:
    det = Detector(effective_options({"min_gap": 5}, env={}), lambda name, frame: None)
    t = feed(det, slide(0), 0, 2)
    feed(det, slide(1), t, 10)
    assert [s.t for s in det.shots] == [1.0, 6.0]  # 5 s nach dem ersten, nicht verworfen
    assert det.shots[1].t_detect == 2.0
