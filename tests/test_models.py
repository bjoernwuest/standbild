"""Modellverwaltung (10.3) – HuggingFace-Aufrufe sind durch Attrappen ersetzt."""

import json
import time
from pathlib import Path
from types import SimpleNamespace

import huggingface_hub
import pytest
from conftest import MODEL_FILES, fake_model

from standbild import models
from standbild.models import ModelError


def hf(
    monkeypatch: pytest.MonkeyPatch, files: dict[str, int], download=None, calls: list | None = None
) -> None:
    """Ersetzt HfApi.model_info und snapshot_download."""

    class Api:
        def model_info(self, repo_id: str, revision=None, files_metadata=False):
            if repo_id == "gibt/esnicht":
                raise RuntimeError("404")
            if calls is not None:
                calls.append((repo_id, revision))
            return SimpleNamespace(
                sha="abc123", siblings=[SimpleNamespace(rfilename=f, size=s) for f, s in files.items()]
            )

    def snapshot_download(repo_id: str, revision=None, local_dir=None, allow_patterns=None, tqdm_class=None):
        assert revision == "abc123" and allow_patterns == models.FILES
        written = tqdm_class(desc="Reconstructing (incomplete total...)", total=0, unit="B", unit_scale=True)
        network = tqdm_class(desc="Downloading bytes", total=0, unit="B")
        file_count = tqdm_class(total=len(files))
        Path(local_dir).mkdir(parents=True)
        (Path(local_dir) / ".cache").mkdir()
        for f, size in files.items():
            (Path(local_dir) / f).write_bytes(b"x" * size)
            if any(f == p or f.startswith(p.rstrip("*")) for p in models.FILES):
                written.update(size)  # nur diese Bytes zählen für den Fortschritt
                network.update(size // 2)
            file_count.update(1)
        if download:
            download()

    monkeypatch.setattr(huggingface_hub, "HfApi", Api)
    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot_download)


def wait_for(condition, timeout: float = 5.0) -> None:
    end = time.time() + timeout
    while not condition():
        assert time.time() < end, "Zeitüberschreitung"
        time.sleep(0.02)


CT2 = {"model.bin": 500, "config.json": 10, "tokenizer.json": 20, "vocabulary.json": 5, "README.md": 99}


def test_seed_is_copied_on_start_and_becomes_default(env: Path) -> None:
    (env / "models").mkdir()
    (env / "models" / "abgebrochen.partial").mkdir()
    models.startup()
    assert models.installed() == ["large-v3-turbo"] and models.default_model() == "large-v3-turbo"
    assert not (env / "models" / "abgebrochen.partial").exists()  # abgebrochene Downloads werden gelöscht
    [m] = models.list_models()
    assert (m["name"], m["status"], m["default"], m["source"]) == (
        "large-v3-turbo",
        "ready",
        True,
        "Startausstattung des Images",
    )
    assert m["size"] == 300 and sorted(p.name for p in models.path("large-v3-turbo").iterdir()) == sorted(
        [*MODEL_FILES, models.META]
    )
    stamp = (models.path("large-v3-turbo") / models.META).read_text()
    models.startup()  # zweiter Start: nichts wird neu kopiert, der Standard bleibt
    assert (models.path("large-v3-turbo") / models.META).read_text() == stamp


def test_start_without_seed_keeps_existing_default(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STANDBILD_SEED_DIR", str(env / "kein-seed"))
    models.get_settings.cache_clear()
    models.startup()
    assert models.installed() == [] and models.default_model() is None and models.list_models() == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Systran/faster-whisper-medium", ("Systran/faster-whisper-medium", None)),
        ("https://huggingface.co/Systran/faster-whisper-medium", ("Systran/faster-whisper-medium", None)),
        ("https://huggingface.co/Systran/faster-whisper-medium/", ("Systran/faster-whisper-medium", None)),
        (
            " https://huggingface.co/Systran/faster-whisper-medium/tree/refs%2Fpr%2F1 ",
            ("Systran/faster-whisper-medium", "refs%2Fpr%2F1"),
        ),
        ("Systran/faster-whisper-small/tree/main", ("Systran/faster-whisper-small", "main")),
    ],
)
def test_parse_source(source: str, expected: tuple) -> None:
    assert models.parse_source(source) == expected


@pytest.mark.parametrize(
    "source", ["", "medium", "https://example.com/Systran/faster-whisper-medium", "a/b/c"]
)
def test_parse_source_rejects(source: str) -> None:
    with pytest.raises(ModelError, match="Kein HuggingFace-Link"):
        models.parse_source(source)


def test_add_valid_ct2_repo(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    models.startup()
    calls: list = []
    hf(monkeypatch, CT2, calls=calls)
    assert models.add("https://huggingface.co/Systran/faster-whisper-medium/tree/main") == "medium"
    wait_for(lambda: "medium" in models.installed())
    assert calls == [("Systran/faster-whisper-medium", "main")]
    meta = json.loads((models.path("medium") / models.META).read_text())
    assert (meta["source"], meta["revision"]) == ("Systran/faster-whisper-medium", "abc123")
    assert (
        not (models.path("medium") / ".cache").exists() and not (env / "models" / "medium.partial").exists()
    )
    assert models.default_model() == "large-v3-turbo"  # der Standard ändert sich nicht von selbst
    with pytest.raises(ModelError, match="Name bereits vorhanden: medium"):
        models.add("Systran/faster-whisper-medium")
    assert models.add("Systran/faster-whisper-medium", "medium-2") == "medium-2"
    wait_for(lambda: "medium-2" in models.installed())
    with pytest.raises(ModelError, match="Ungültiger Name"):
        models.add("Systran/faster-whisper-medium", "../böse")


def test_repo_without_model_bin_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    models.startup()
    hf(monkeypatch, {"pytorch_model.bin": 500, "config.json": 10, "tokenizer.json": 20})
    with pytest.raises(
        ModelError, match="Kein CTranslate2-Modell. Bitte eine faster-whisper-Variante wählen."
    ):
        models.add("openai/whisper-medium")
    hf(monkeypatch, {"model.bin": 500, "config.json": 10})  # weder tokenizer.json noch vocabulary.*
    with pytest.raises(ModelError, match="Kein CTranslate2-Modell"):
        models.add("Systran/faster-whisper-medium")
    with pytest.raises(ModelError, match="Repo nicht gefunden oder nicht zugänglich"):
        models.add("gibt/esnicht")
    assert models.installed() == ["large-v3-turbo"] and len(models.list_models()) == 1


def test_progress_and_aborted_download(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    models.startup()
    seen: list[dict] = []

    def abort() -> None:
        seen.extend(m for m in models.list_models() if m["name"] == "medium")  # während des Downloads
        raise ConnectionError("Verbindung abgebrochen")

    hf(monkeypatch, CT2, download=abort)
    models.add("Systran/faster-whisper-medium")
    wait_for(lambda: any(m["status"] == "error" for m in models.list_models()))
    assert seen[0]["status"] == "downloading" and 0.9 < seen[0]["progress"] <= 0.99
    [failed] = [m for m in models.list_models() if m["name"] == "medium"]
    assert "Verbindung abgebrochen" in failed["error"] and failed["size"] == 535
    assert "medium" not in models.installed() and not (env / "models" / "medium.partial").exists()
    hf(monkeypatch, CT2)  # derselbe Name darf nach einem Fehlschlag erneut versucht werden
    models.add("Systran/faster-whisper-medium")
    wait_for(lambda: "medium" in models.installed())
    assert [m["status"] for m in models.list_models()] == ["ready", "ready"]


def test_default_and_remove(env: Path) -> None:
    models.startup()
    fake_model(env / "models" / "small")
    (env / "models" / "small" / models.META).write_text(
        '{"source": "Systran/faster-whisper-small", "size": 300}'
    )
    with pytest.raises(ModelError, match="Das Standardmodell kann nicht entfernt werden"):
        models.remove("large-v3-turbo")
    with pytest.raises(ModelError, match="wird gerade von einem Auftrag verwendet"):
        models.remove("small", in_use=True)
    with pytest.raises(ModelError, match="nicht installiert"):
        models.remove("fehlt")
    with pytest.raises(ModelError, match="nicht installiert"):
        models.set_default("fehlt")
    models.set_default("small")
    assert models.default_model() == "small" and [m["default"] for m in models.list_models()] == [False, True]
    models.remove("large-v3-turbo")
    assert models.installed() == ["small"]
