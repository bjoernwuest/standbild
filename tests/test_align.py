"""Einfügeregel (8.1) und Absätze (8.2) mit künstlichen Wort-Zeitstempeln."""

from standbild.pipeline.align import build_body
from standbild.pipeline.detect import Shot


def words(text: str, start: float, step: float = 0.5) -> list[dict]:
    """Jedes Wort dauert ``step`` Sekunden, ohne Pause dazwischen."""
    return [
        {"start": start + i * step, "end": start + (i + 1) * step, "word": " " + w}
        for i, w in enumerate(text.split())
    ]


def seg(text: str, start: float, step: float = 0.5) -> dict:
    ws = words(text, start, step)
    return {"start": ws[0]["start"], "end": ws[-1]["end"], "text": text, "words": ws}


def shot(n: int, t_detect: float, t: float | None = None, file: str | None = None) -> Shot:
    return Shot(
        n, file or f"frame_{n:04d}.png", t if t is not None else t_detect + 1, t_detect, 0.5, 0.5, "global"
    )


def blocks(*args, **kw) -> list[str]:
    return build_body(*args, **kw).strip().split("\n\n")


# Zwei Sätze: "Erster Satz hier." endet bei 1,5 s, "Zweiter Satz folgt dann." läuft von 1,5 bis 3,5 s.
TWO = [seg("Erster Satz hier. Zweiter Satz folgt dann.", 0.0)]


def test_marker_goes_to_nearest_sentence_boundary_within_tolerance() -> None:
    out = blocks(TWO, [shot(1, 2.4)])  # mitten im zweiten Satz, 0,9 s nach der Grenze
    assert out == [
        "[00:00:00] Erster Satz hier.",
        ">>>>> ![frame_0001 · 00:00:03](frames/frame_0001.png)",
        "[00:00:01] Zweiter Satz folgt dann.",
    ]


def test_no_sentence_is_torn_when_a_boundary_is_in_reach() -> None:
    out = blocks(TWO, [shot(1, 0.6)])
    assert out[0].startswith(">>>>>") and out[1] == "[00:00:00] Erster Satz hier. Zweiter Satz folgt dann."


def test_very_long_sentence_falls_back_to_word_boundary() -> None:
    long = [
        seg(
            "eins zwei drei vier fünf sechs sieben acht neun zehn elf zwölf dreizehn vierzehn fünfzehn sechzehn Ende.",
            0.0,
            1.0,
        )
    ]
    out = blocks(long, [shot(1, 8.2)])  # Satzgrenzen bei 0 s und 17 s: beide weiter als 4 s entfernt
    assert out == ["[00:00:00] eins zwei drei vier fünf sechs sieben acht", ">>>>> ![frame_0001 · 00:00:09](frames/frame_0001.png)",
                   "[00:00:08] neun zehn elf zwölf dreizehn vierzehn fünfzehn sechzehn Ende."]  # fmt: skip


def test_long_sentence_prefers_a_comma_over_an_arbitrary_word() -> None:
    long = [
        seg(
            "eins zwei drei vier fünf sechs sieben, acht neun zehn elf zwölf dreizehn vierzehn fünfzehn sechzehn Ende.",
            0.0,
            1.0,
        )
    ]
    out = blocks(long, [shot(1, 8.2)])
    assert out[0] == "[00:00:00] eins zwei drei vier fünf sechs sieben," and out[2].startswith(
        "[00:00:07] acht"
    )


def test_several_shots_on_one_boundary_keep_time_order() -> None:
    out = blocks(TWO, [shot(2, 1.9), shot(1, 1.2)])
    assert [b[:22] for b in out[1:3]] == [">>>>> ![frame_0001 · 0", ">>>>> ![frame_0002 · 0"]
    assert out[0] == "[00:00:00] Erster Satz hier." and out[3] == "[00:00:01] Zweiter Satz folgt dann."


def test_shot_in_speech_pause_sits_between_paragraphs() -> None:
    segments = [
        seg("Jetzt die Demo", 0.0),
        seg("und weiter im Text.", 21.5),
    ]  # 20 s stumme Demo, kein Satzzeichen
    out = blocks(segments, [shot(1, 5.0), shot(2, 12.0)])
    assert out == ["[00:00:00] Jetzt die Demo", ">>>>> ![frame_0001 · 00:00:06](frames/frame_0001.png)",
                   ">>>>> ![frame_0002 · 00:00:13](frames/frame_0002.png)", "[00:00:21] und weiter im Text."]  # fmt: skip


def test_pause_of_two_seconds_starts_a_paragraph_and_short_pause_does_not() -> None:
    out = blocks([seg("Eins.", 0.0), seg("Zwei.", 1.5), seg("Drei.", 4.0)], [])
    assert out == ["[00:00:00] Eins. Zwei.", "[00:00:04] Drei."]


def test_shots_before_and_after_all_speech_and_without_speech() -> None:
    out = blocks([seg("Nur ein Satz.", 10.0)], [shot(1, 0.0), shot(2, 60.0)])
    assert (
        out[0].startswith(">>>>> ![frame_0001")
        and out[1].startswith("[00:00:10]")
        and out[2].startswith(">>>>> ![frame_0002")
    )
    assert blocks([], [shot(1, 0.0)]) == [">>>>> ![frame_0001 · 00:00:01](frames/frame_0001.png)"]


def test_subtitles_without_word_timestamps_align_on_segment_level() -> None:
    segments = [{"start": 0.0, "end": 3.0, "text": "erste Zeile ohne Punkt", "words": []},
                {"start": 3.0, "end": 6.0, "text": "zweite Zeile", "words": []}]  # fmt: skip
    out = blocks(segments, [shot(1, 2.5)])
    assert out == [
        "[00:00:00] erste Zeile ohne Punkt",
        ">>>>> ![frame_0001 · 00:00:03](frames/frame_0001.png)",
        "[00:00:03] zweite Zeile",
    ]


def test_duplicate_reuses_earlier_path_with_current_time() -> None:
    dup = Shot(1, "frame_0001_00-00-01.png", 125.0, 124.0, 0.5, 0.5, "global", "frame_0001_00-00-01.png")
    assert blocks([], [dup]) == [">>>>> ![frame_0001 · 00:02:05](frames/frame_0001_00-00-01.png)"]


def test_timestamps_can_be_disabled_or_linked_to_youtube() -> None:
    assert blocks(TWO, [], timestamps=False) == ["Erster Satz hier. Zweiter Satz folgt dann."]
    hour = [seg("Spät im Video.", 3754.0)]
    assert blocks(hour, [], youtube_id="abc123DEF45") == [
        "[01:02:34](https://www.youtube.com/watch?v=abc123DEF45&t=3754s) Spät im Video."
    ]
