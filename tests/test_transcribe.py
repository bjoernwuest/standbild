"""Transkription: Wiederholungsfilter, Untertitel-Umwandlung und ein langsamer Test mit dem Modell tiny."""

from pathlib import Path

import pytest

from standbild.pipeline.transcribe import drop_repeats, from_vtt, load_wav, transcribe

JFK = Path(__file__).parent / "data" / "jfk.wav"


def test_more_than_three_literal_repeats_are_removed() -> None:
    texts = [
        "Hallo.",
        "Danke.",
        "Danke.",
        "Danke.",
        "Weiter.",
        "Thank you.",
        "Thank you.",
        "thank you.",
        "Thank you. ",
        "Ende.",
    ]
    kept, removed = drop_repeats(
        [{"start": i, "end": i + 1, "text": t, "words": []} for i, t in enumerate(texts)]
    )
    assert [s["text"] for s in kept] == [
        "Hallo.",
        "Danke.",
        "Danke.",
        "Danke.",
        "Weiter.",
        "Ende.",
    ]  # dreimal ist erlaubt
    assert removed == 4 and [s["id"] for s in kept] == [0, 1, 2, 3, 4, 5]


def test_manual_subtitles_become_segments_without_words() -> None:
    vtt = (
        "WEBVTT\nKind: captions\nLanguage: de\n\n"
        "00:00:01.000 --> 00:00:03.500 align:start\nHallo &amp; <b>willkommen</b>\nzweite Zeile\n\n"
        "NOTE Kommentar\n\n"
        "1\n01:02:03.250 --> 01:02:05.000\nSpät im Video\n\n"
        "00:10.000 --> 00:12.000\nOhne Stunden\n"
    )
    out = from_vtt(vtt, "de")
    assert out["language"] == "de" and out["removed_repetitions"] == 0
    assert [(s["id"], s["start"], s["end"], s["text"], s["words"]) for s in out["segments"]] == [
        (0, 1.0, 3.5, "Hallo & willkommen zweite Zeile", []),
        (1, 3723.25, 3725.0, "Spät im Video", []),
        (2, 10.0, 12.0, "Ohne Stunden", []),
    ]


def test_wav_must_be_16k_mono(tmp_path: Path) -> None:
    assert load_wav(JFK).shape == (176000,)  # 11 s bei 16 kHz
    import wave

    with wave.open(str(tmp_path / "stereo.wav"), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\0" * 400)
    with pytest.raises(ValueError, match="16 kHz, mono"):
        load_wav(tmp_path / "stereo.wav")


@pytest.mark.slow
def test_tiny_model_transcribes_jfk(tmp_path: Path) -> None:
    from faster_whisper.utils import download_model

    model_dir = Path(
        download_model("tiny", output_dir=str(Path.home() / ".cache" / "standbild-test" / "tiny"))
    )
    seen: list[float] = []
    out = transcribe(JFK, model_dir, progress=seen.append)
    text = " ".join(s["text"] for s in out["segments"]).lower()
    assert out["language"] == "en" and "ask not what your country can do for you" in text
    words = [w for s in out["segments"] for w in s["words"]]
    assert len(words) > 15 and all(0 <= w["start"] <= w["end"] <= 11.5 for w in words)
    assert seen and seen[-1] == pytest.approx(1.0, abs=0.2)
    assert set(out["segments"][0]) == {"id", "start", "end", "text", "words"}


def test_title_hint_conditioning_and_invented_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Der Titel geht als Hotwords an Whisper; erfundene Segmente (am Audioende, unsprechbar schnell) fliegen raus."""
    import sys
    from types import SimpleNamespace as NS

    module = sys.modules["standbild.pipeline.transcribe"]  # das Paket exportiert unter dem Namen die Funktion

    calls: list[dict] = []

    class FakeModel:
        def transcribe(self, samples, **kw):
            calls.append(kw)
            word = NS(start=1.0, end=1.5, word=" Hallo.")
            real = NS(start=1.0, end=1.5, text=" Hallo.", words=[word])
            invented = NS(start=10.95, end=11.2, text=" Thank you.", words=[])
            crammed = NS(start=9.30, end=9.38, text=" Now I will see you through the project.", words=[])
            return iter([real, crammed, invented]), NS(language="de", duration=11.0)

    monkeypatch.setattr(module, "load_model", lambda model_dir, device: FakeModel())
    out = transcribe(JFK, Path("modell"), lang="de", hint="SAP LeanIX Demo")
    assert [s["text"] for s in out["segments"]] == ["Hallo."] and out["removed_repetitions"] == 2
    assert calls[0]["hotwords"] == "SAP LeanIX Demo" and calls[0]["condition_on_previous_text"] is True
    assert (
        calls[0]["language"] == "de"
        and calls[0]["vad_filter"] is True
        and calls[0]["word_timestamps"] is True
    )
    transcribe(JFK, Path("modell"), condition_on_previous_text=False)
    assert (
        calls[1]["hotwords"] is None
        and calls[1]["condition_on_previous_text"] is False
        and calls[1]["language"] is None
    )
