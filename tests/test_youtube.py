"""URL-Normalisierung (9.1)."""

import pytest

from standbild.youtube import extract_id, watch_url

VALID = [
    "https://www.youtube.com/watch?v=dCG6P_Sx7qM",
    "https://youtube.com/watch?v=dCG6P_Sx7qM",
    "http://www.youtube.com/watch?v=dCG6P_Sx7qM",
    "https://m.youtube.com/watch?v=dCG6P_Sx7qM",
    "https://music.youtube.com/watch?v=dCG6P_Sx7qM",
    "www.youtube.com/watch?v=dCG6P_Sx7qM",
    "https://www.youtube.com/watch?v=dCG6P_Sx7qM&t=754s",
    "https://www.youtube.com/watch?list=PL123&v=dCG6P_Sx7qM&index=2",
    "https://www.youtube.com/watch?v=dCG6P_Sx7qM&si=AbCdEfGh",
    "https://youtu.be/dCG6P_Sx7qM",
    "https://youtu.be/dCG6P_Sx7qM?si=AbCdEfGh&t=12",
    "https://www.youtube.com/shorts/dCG6P_Sx7qM",
    "https://www.youtube.com/embed/dCG6P_Sx7qM?start=30",
    "https://www.youtube.com/live/dCG6P_Sx7qM?feature=share",
    "https://www.youtube-nocookie.com/embed/dCG6P_Sx7qM",
    "  https://youtu.be/dCG6P_Sx7qM  ",
]
INVALID = [
    "",
    "dCG6P_Sx7qM",
    "kein link",
    "https://www.youtube.com/",
    "https://www.youtube.com/watch",
    "https://www.youtube.com/watch?v=",
    "https://www.youtube.com/watch?v=zu-kurz",
    "https://www.youtube.com/watch?v=dCG6P_Sx7qMXX",
    "https://www.youtube.com/watch?v=dCG6P_Sx7q!",
    "https://www.youtube.com/playlist?list=PL123",
    "https://www.youtube.com/channel/dCG6P_Sx7qM",
    "https://youtu.be/",
    "https://vimeo.com/123456789",
    "https://youtube.com.evil.example/watch?v=dCG6P_Sx7qM",
    "https://example.com/?u=https://youtu.be/dCG6P_Sx7qM",
    "ftp://youtube.com/watch?v=dCG6P_Sx7qM",
    "javascript:alert(1)",
]


@pytest.mark.parametrize("url", VALID)
def test_valid(url: str) -> None:
    assert extract_id(url) == "dCG6P_Sx7qM"


@pytest.mark.parametrize("url", INVALID)
def test_invalid(url: str) -> None:
    with pytest.raises(ValueError, match="Kein gültiger YouTube-Link"):
        extract_id(url)


def test_watch_url_round_trip() -> None:
    assert extract_id(watch_url("abc-_123XYZ")) == "abc-_123XYZ"
