"""Parameter: Rangfolge Auftrag > Profil > Umgebung > eingebaut, Wertebereiche (Abschnitt 4)."""

import pytest

from standbild.config import PROFILES, Options, OptionsError, Settings, allowed, effective_options


def test_builtin_defaults_match_spec() -> None:
    o = effective_options({}, env={})
    assert (o["profile"], o["threshold"], o["min_local_change"], o["stable_time"], o["min_gap"]) == (
        "default",
        0.05,
        0.004,
        1.0,
        3.0,
    )
    assert (o["sample_interval"], o["pixel_noise"], o["max_wait"], o["max_per_minute"]) == (0.5, 20, 8, 6)
    assert (o["max_height"], o["analysis_width"], o["lang"], o["use_subtitles"], o["masks"]) == (
        1080,
        960,
        "auto",
        False,
        [],
    )
    assert {k: o[k] for k in PROFILES["default"]} == PROFILES["default"]


def test_precedence_job_over_profile_over_env_over_builtin() -> None:
    env = {"STANDBILD_THRESHOLD": "0.1", "STANDBILD_MAX_WAIT": "12", "STANDBILD_PIXEL_NOISE": "30"}
    assert effective_options({}, env=env)["threshold"] == 0.1  # Umgebung schlägt eingebaut
    o = effective_options({"profile": "coding", "min_gap": 9, "pixel_noise": 25}, env=env)
    assert o["threshold"] == 0.02  # Profil des Auftrags schlägt Umgebung
    assert o["stable_time"] == 1.5 and o["min_local_change"] == 0.002
    assert o["min_gap"] == 9  # Auftrag schlägt Profil
    assert o["pixel_noise"] == 25  # Auftrag schlägt Umgebung
    assert o["max_wait"] == 12  # Umgebung bleibt, wo weder Auftrag noch Profil etwas sagen


def test_env_profile_and_env_values() -> None:
    o = effective_options({}, env={"STANDBILD_PROFILE": "slides", "STANDBILD_MIN_GAP": "7"})
    assert (o["profile"], o["threshold"], o["min_gap"]) == ("slides", 0.08, 7.0)
    o = effective_options(
        {}, env={"STANDBILD_MASKS": "[[0.75, 0.75, 0.25, 0.25]]", "STANDBILD_USE_SUBTITLES": "true"}
    )
    assert o["masks"] == [[0.75, 0.75, 0.25, 0.25]] and o["use_subtitles"] is True


def test_none_means_not_set() -> None:
    assert effective_options({"threshold": None}, env={})["threshold"] == 0.05


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("threshold", 0.95), ("threshold", 0), ("min_local_change", 0.6), ("pixel_noise", 0), ("pixel_noise", 129),
        ("stable_time", 11), ("max_wait", 0.5), ("min_gap", 121), ("max_per_minute", 61), ("analysis_width", 200),
        ("max_height", 4320), ("sample_interval", 0.05), ("profile", "movie"), ("lang", "deutsch"),
        ("masks", [[0.1, 0.2, 0.3]]), ("masks", [[0, 0, 1.5, 1]]), ("use_subtitles", "vielleicht"),
    ],
)  # fmt: skip
def test_out_of_range_is_rejected_with_the_allowed_range(key: str, value: object) -> None:
    with pytest.raises(OptionsError) as exc:
        effective_options({key: value}, env={})
    assert list(exc.value.errors) == [key]
    assert allowed(key) in exc.value.errors[key]


def test_unknown_key_and_bad_env() -> None:
    with pytest.raises(OptionsError) as exc:
        effective_options({"treshold": 0.1}, env={})
    assert exc.value.errors == {"treshold": "Unbekannter Parameter"}
    with pytest.raises(OptionsError, match="masks"):
        effective_options({}, env={"STANDBILD_MASKS": "kein json"})


def test_allowed_texts() -> None:
    assert allowed("profile") == "slides, default, coding"
    assert allowed("threshold") == "0.001 bis 0.9"
    assert allowed("use_subtitles") == "true oder false"
    assert allowed("gibt-es-nicht") == "unbekannter Parameter"
    assert set(Options.model_fields) >= {"profile", "whisper_model", "lang", "masks", "max_per_minute"}


def test_settings_conninfo(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="STANDBILD_DB_HOST.*STANDBILD_DB_PASSWORD"):
        Settings().conninfo()
    secret = tmp_path / "pw"
    secret.write_text("geheim\n")
    s = Settings(db_host="db.local", db_name="standbild", db_user="u", db_password_file=secret, db_schema="x")
    info = s.conninfo()
    assert (
        "host=db.local" in info
        and "password=geheim" in info
        and "search_path=x" in info
        and "port=5432" in info
    )
