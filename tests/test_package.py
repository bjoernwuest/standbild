"""Paket (8.3–8.5): Bildverweise zeigen ins ZIP, AGENTS.md ist vollständig; Pipeline Ende-zu-Ende ohne Ton."""

import json
import re
import sys
import zipfile
from pathlib import Path

import cv2
import pytest
from test_detect import H, W, hold, slide, write_video

from standbild.config import effective_options
from standbild.pipeline import run_pipeline
from standbild.pipeline.fetch import FetchError, explain

MARKER = re.compile(r"^>>>>> !\[frame_(\d{4}) · \d\d:\d\d:\d\d\]\((frames/[^)]+)\)$", re.M)
SECTIONS = [
    "Was das Paket ist",
    "Dateien",
    "Lesart der Markierung",
    "Hinweise zur Auswertung",
    "Empfohlenes Vorgehen",
]


@pytest.fixture
def package(tmp_path: Path) -> Path:
    video = write_video(
        tmp_path / "Mein Video.mp4", hold(slide(0), 5) + hold(slide(1), 5) + hold(slide(0), 5)
    )
    stages: list[str] = []
    result = run_pipeline(
        str(video),
        effective_options({"whisper_model": "large-v3-turbo"}, env={}),
        workdir=tmp_path / "tmp" / "job",
        out_dir=tmp_path / "out",
        model_dir=tmp_path / "models" / "large-v3-turbo",
        progress=lambda stage, p: stages.append(stage),
    )
    assert result.screenshots == 2 and result.title == "Mein Video" and result.video_id == "Mein-Video"
    assert [s for i, s in enumerate(stages) if s not in stages[:i]] == [
        "download",
        "transcribe",
        "detect",
        "assemble",
        "store",
    ]
    assert not (tmp_path / "tmp" / "job").exists()  # Arbeitsverzeichnis ist wieder weg
    assert not list((tmp_path / "out").glob("*.part"))
    return result.package


def test_package_is_self_contained(package: Path) -> None:
    assert re.fullmatch(r"Mein-Video_\d{8}-\d{4}\.zip", package.name)
    with zipfile.ZipFile(package) as z:
        names = set(z.namelist())
        transcript = z.read("transcript.md").decode()
        meta = json.loads(z.read("meta.json"))
        frames = json.loads(z.read("log/frames.json"))
        log = z.read("log/processing.log").decode()
        csv_head = z.read("log/detection_log.csv").decode().splitlines()[0]
        png = z.read("frames/frame_0001_00-00-01.png")
    assert {
        "AGENTS.md",
        "transcript.md",
        "meta.json",
        "log/processing.log",
        "log/frames.json",
        "log/detection_log.csv",
    } <= names
    markers = MARKER.findall(transcript)
    assert [n for n, _ in markers] == [
        "0001",
        "0002",
        "0001",
    ]  # Rückkehr zur ersten Folie verweist aufs erste Bild
    assert all(path in names for _, path in markers)
    assert sum(n.startswith("frames/") for n in names) == 2
    assert (
        transcript.startswith('---\ntitle: "Mein Video"\nsource: Mein Video.mp4\n')
        and "\n# Mein Video\n" in transcript
    )
    assert "screenshots: 2\n" in transcript and "tool: standbild " in transcript
    assert (meta["width"], meta["height"], meta["screenshots"], meta["duplicates"]) == (W, H, 2, 1)
    assert meta["options"]["threshold"] == 0.05 and "standbild" in meta["versions"]
    assert [f["duplicate_of"] for f in frames] == [None, None, "frame_0001_00-00-01.png"]
    assert set(frames[0]) == {
        "file",
        "t",
        "t_detect",
        "global_ratio",
        "largest_region",
        "reason",
        "duplicate_of",
    }
    assert csv_head == "t,global_ratio,largest_region,motion,blank,state,action"
    for needle in (
        "Versionen:",
        "Wirksame Einstellungen:",
        "Stufe download: Ende",
        "Stufe detect: Ende",
        "Keine Tonspur",
        "Screenshots: 2, Duplikate: 1",
    ):
        assert needle in log
    tmp = package.parent / "frame.png"
    tmp.write_bytes(png)
    assert cv2.imread(str(tmp)).shape == (H, W, 3)  # exakt die Auflösung des Videos


def test_agents_md_has_all_mandatory_sections(package: Path) -> None:
    with zipfile.ZipFile(package) as z:
        agents = z.read("AGENTS.md").decode()
    assert len(agents.splitlines()) < 60
    for head in ("Titel", "Kanal", "Quelle", "Dauer", "Sprache", "Verarbeitet am", "Screenshots"):
        assert f"**{head}:**" in agents
    assert [s for s in SECTIONS if f"## {s}" in agents] == SECTIONS
    assert (
        ">>>>> ![" in agents
        and "`transcript.md`" in agents
        and "`frames/`" in agents
        and "`meta.json`" in agents
        and "`log/`" in agents
    )
    assert (
        "Schreibweise im Bild hat Vorrang" in agents and "Zusammenfass" not in agents
    )  # keine Aufgabe vorgegeben


def test_template_dir_overrides_builtin_template(tmp_path: Path) -> None:
    (tmp_path / "tpl").mkdir()
    (tmp_path / "tpl" / "AGENTS.md.j2").write_text("Eigene Vorlage für {{ title }}\n")
    video = write_video(tmp_path / "v.mp4", hold(slide(0), 3))
    opts = effective_options({"whisper_model": "x"}, env={})
    result = run_pipeline(
        str(video),
        opts,
        workdir=tmp_path / "w",
        out_dir=tmp_path,
        model_dir=tmp_path,
        template_dir=tmp_path / "tpl",
    )
    with zipfile.ZipFile(result.package) as z:
        assert z.read("AGENTS.md").decode() == "Eigene Vorlage für v\n"


def test_fetch_errors_are_clear_and_workdir_is_removed(tmp_path: Path) -> None:
    opts = effective_options({}, env={})
    with pytest.raises(FetchError, match="Datei nicht gefunden"):
        run_pipeline(
            str(tmp_path / "fehlt.mp4"), opts, workdir=tmp_path / "w", out_dir=tmp_path, model_dir=tmp_path
        )
    (tmp_path / "kaputt.mp4").write_text("kein Video")
    with pytest.raises(FetchError, match="Keine lesbare Videodatei"):
        run_pipeline(
            str(tmp_path / "kaputt.mp4"), opts, workdir=tmp_path / "w", out_dir=tmp_path, model_dir=tmp_path
        )
    assert not (tmp_path / "w").exists()


def test_download_errors_are_explained() -> None:
    assert (
        explain(Exception("ERROR: [youtube] x: Sign in to confirm your age"))
        == "Video ist altersbeschränkt (cookies.txt nötig: --cookies)"
    )
    assert explain(Exception("ERROR: [youtube] x: Private video")) == "Video ist privat"
    assert explain(Exception("ERROR: [youtube] x: Video unavailable")) == "Video ist nicht verfügbar"
    assert explain(Exception("ERROR: [youtube] x: nsig extraction failed")).endswith(
        "yt-dlp evtl. veraltet – Image neu bauen"
    )


class FakeYDL:
    """Attrappe für yt_dlp.YoutubeDL: "lädt" ein synthetisches Video und optional Untertitel."""

    video: Path
    failures: list[str] = []
    seen: list[dict] = []

    def __init__(self, opts: dict) -> None:
        self.opts = opts
        FakeYDL.seen.append(dict(opts))

    def __enter__(self) -> "FakeYDL":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def extract_info(self, url: str, download: bool = True) -> dict:
        import shutil

        if self.opts["writesubtitles"] and "subs-429" in FakeYDL.failures:
            raise RuntimeError(
                "ERROR: Unable to download video subtitles for 'en': HTTP Error 429: Too Many Requests"
            )
        if FakeYDL.failures and FakeYDL.failures[0].startswith("ERROR"):
            raise RuntimeError(FakeYDL.failures.pop(0))
        target = Path(self.opts["outtmpl"].replace("%(ext)s", "mp4"))
        shutil.copy(FakeYDL.video, target)
        self.opts["progress_hooks"][0]({"status": "downloading", "downloaded_bytes": 5, "total_bytes": 10})
        if self.opts["writesubtitles"]:
            target.with_name("source.en.vtt").write_text(
                "WEBVTT\n\n00:00:00.500 --> 00:00:04.000\nErste Folie.\n\n00:00:05.500 --> 00:00:09.000\nZweite Folie.\n"
            )
        chapters = [{"start_time": 0.0, "end_time": 5.0, "title": "Intro"}]
        return {"id": "aaaaaaaaaaa", "title": "Ein Titel", "channel": "Kanal", "upload_date": "20261101", "language": "en-US",
                "webpage_url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "description": "Text", "chapters": chapters}  # fmt: skip


@pytest.fixture
def ydl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> type[FakeYDL]:
    import yt_dlp

    FakeYDL.video = write_video(tmp_path / "quelle.mp4", hold(slide(0), 5) + hold(slide(1), 5))
    FakeYDL.failures, FakeYDL.seen = [], []
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    # das Paket exportiert unter demselben Namen die Funktion, deshalb über sys.modules
    monkeypatch.setattr(sys.modules["standbild.pipeline.fetch"], "DOWNLOAD_RETRY_PAUSE", 0)
    return FakeYDL


def test_youtube_source_with_manual_subtitles(tmp_path: Path, ydl: type[FakeYDL]) -> None:
    opts = effective_options({"use_subtitles": True, "max_height": 720}, env={})
    seen: list[float] = []
    result = run_pipeline(
        "https://youtu.be/aaaaaaaaaaa", opts, workdir=tmp_path / "w", out_dir=tmp_path / "out", model_dir=tmp_path,
        progress=lambda stage, p: seen.append(p) if stage == "download" else None,
    )  # fmt: skip
    assert (
        ydl.seen[0]["format"] == "bv*[height<=720]+ba/b[height<=720]"
        and ydl.seen[0]["writeautomaticsub"] is False
    )
    assert 0.5 in seen and re.fullmatch(r"aaaaaaaaaaa_\d{8}-\d{4}\.zip", result.package.name)
    with zipfile.ZipFile(result.package) as z:
        transcript, meta, agents = (
            z.read("transcript.md").decode(),
            json.loads(z.read("meta.json")),
            z.read("AGENTS.md").decode(),
        )
    assert "[00:00:00](https://www.youtube.com/watch?v=aaaaaaaaaaa&t=0s) Erste Folie." in transcript
    assert "[00:00:05](https://www.youtube.com/watch?v=aaaaaaaaaaa&t=5s) Zweite Folie." in transcript
    assert (
        "source: https://www.youtube.com/watch?v=aaaaaaaaaaa\n" in transcript
        and "manuelle Untertitel" in transcript
    )
    assert meta["chapters"] == [{"start": 0.0, "title": "Intro"}] and meta["transcript_source"] == "subtitles"
    assert (meta["channel"], meta["upload_date"], meta["whisper_model"]) == ("Kanal", "20261101", None)
    assert "manuell erstellten Untertiteln" in agents and "dieses Video hat keine" not in agents


def test_download_is_retried_and_subtitle_failure_falls_back(tmp_path: Path, ydl: type[FakeYDL]) -> None:
    from standbild.pipeline.fetch import fetch

    ydl.failures = ["ERROR: unable to download video data: HTTP Error 403: Forbidden", "subs-429"]
    (tmp_path / "w").mkdir()
    meta, video, audio, subs = fetch(
        "https://youtu.be/aaaaaaaaaaa", tmp_path / "w", effective_options({"use_subtitles": True}, env={})
    )
    assert [o["writesubtitles"] for o in ydl.seen] == [
        True,
        False,
        False,
    ]  # ohne Untertitel weiter, dann 403 wiederholt
    assert (meta["title"], video.name, audio, subs) == ("Ein Titel", "source.mp4", None, None)

    ydl.failures = ["ERROR: unable to download video data: HTTP Error 403: Forbidden"] * 3
    with pytest.raises(FetchError, match="yt-dlp evtl. veraltet"):
        fetch("https://youtu.be/aaaaaaaaaaa", tmp_path / "w", effective_options({}, env={}))
    ydl.failures = ["ERROR: [youtube] aaaaaaaaaaa: Private video. Sign in if you've been granted access"]
    ydl.seen.clear()
    with pytest.raises(FetchError, match="Video ist privat"):
        fetch("https://youtu.be/aaaaaaaaaaa", tmp_path / "w", effective_options({}, env={}))
    assert len(ydl.seen) == 1  # kein neuer Versuch bei endgültigen Fehlern
