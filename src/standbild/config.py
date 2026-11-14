"""Konfiguration: Betriebswerte, Verarbeitungsparameter (Tabelle 4.3), Profile, Rangfolge, Konstanten."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

# --- Konstanten der Spezifikation (nicht pro Auftrag einstellbar) ---------------------------
MOTION_QUIET = 0.002  # 7.5: Bewegung unter 0,2 % gilt als Ruhe
BLANK_MEAN = 10.0  # 7.2: fast schwarze Frames
BLANK_STD = 3.0  # 7.2: einfarbige Frames
RATE_WINDOW = 60.0  # 7.5: Fenster für max_per_minute
DUP_HAMMING = 4  # 7.6: pHash-Abstand
DUP_SSIM = 0.98  # 7.6
ALIGN_TOLERANCE = 4.0  # 8.1: größter Abstand Markierung ↔ Satzgrenze
SENTENCE_PAUSE = 0.8  # 8.1: Sprechpause als Satzgrenze
PARAGRAPH_PAUSE = 2.0  # 8.2: Sprechpause als Absatzgrenze
MAX_REPEATS = 3  # 6: mehr wörtliche Wiederholungen gelten als Whisper-Halluzination
TAIL_MARGIN = 0.2  # 6: Segmente, die in den letzten 0,2 s des Tons beginnen, sind Halluzinationen
MAX_SPEECH_RATE = 40.0  # 6: Zeichen pro Sekunde; mehr kann niemand sprechen → Halluzination
DOWNLOAD_ATTEMPTS = 3  # 5: vorübergehende HTTP-Fehler von YouTube
DOWNLOAD_RETRY_PAUSE = 5.0
PROGRESS_INTERVAL = 2.0  # 6: Fortschritt höchstens alle 2 s in die Datenbank
STALE_AFTER = 300  # 9.3: Heartbeat älter als 5 min → Auftrag zurück in die Warteschlange
MAX_ATTEMPTS = 3  # 9.3
FAILED_RETENTION_DAYS = 30  # 9.3
MAINTENANCE_INTERVAL = 60.0
RECENT_JOBS = 20  # 10.1
SEED_DIR = Path("/opt/standbild/seed-models")
STAGES = ("download", "transcribe", "detect", "assemble", "store")


class Settings(BaseSettings):
    """Betriebswerte aus STANDBILD_* (Abschnitt 4.2)."""

    model_config = SettingsConfigDict(env_prefix="STANDBILD_")

    db_host: str | None = None
    db_port: int = 5432
    db_name: str | None = None
    db_user: str | None = None
    db_password: str | None = None
    db_password_file: Path | None = None
    db_schema: str = "public"
    db_sslmode: str = "prefer"
    port: int = 8080
    public_url: str = "http://localhost:8080"
    workers: int = Field(default=1, ge=1)
    api_token: str = ""
    device: Literal["auto", "cpu", "cuda"] = "auto"
    keep_results: int = Field(default=3, ge=1)
    mcp_max_inline_mb: int = 25
    log_level: str = "INFO"
    template_dir: Path | None = None
    ytdlp_autoupdate: bool = False
    condition_on_previous_text: bool = (
        True  # Whisper; false = Verhalten laut Spezifikation (siehe DECISIONS.md)
    )
    # Feste Volumes (Abschnitt 12); nur für Tests und Läufe außerhalb des Containers überschreibbar.
    tmp_dir: Path = Path("/tmp/standbild")
    packages_dir: Path = Path("/data/packages")
    models_dir: Path = Path("/models")
    seed_dir: Path = SEED_DIR

    def conninfo(self) -> str:
        from psycopg.conninfo import make_conninfo

        missing = [n for n in ("db_host", "db_name", "db_user") if not getattr(self, n)]
        password = self.db_password_file.read_text().strip() if self.db_password_file else self.db_password
        if password is None:
            missing.append("db_password")
        if missing:
            raise RuntimeError(
                "Fehlende Umgebungsvariablen: " + ", ".join(f"STANDBILD_{n.upper()}" for n in missing)
            )
        return make_conninfo(
            host=self.db_host,
            port=self.db_port,
            dbname=self.db_name,
            user=self.db_user,
            password=password,
            sslmode=self.db_sslmode,
            options=f"-c search_path={self.db_schema}",
            application_name="standbild",
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


# --- Verarbeitungsparameter (Tabelle 4.3) -----------------------------------------------------
Mask = Annotated[list[Annotated[float, Field(ge=0, le=1)]], Field(min_length=4, max_length=4)]


class Options(BaseModel):
    """Verarbeitungsparameter eines Auftrags. Alle Schlüssel sind optional."""

    model_config = ConfigDict(extra="forbid")

    profile: Literal["slides", "default", "coding"] = Field(
        "default", description="Satz von Schwellen (threshold, min_local_change, stable_time, min_gap)"
    )
    whisper_model: str | None = Field(None, description="Installiertes Whisper-Modell; leer = Standardmodell")
    lang: str = Field(
        "auto", pattern=r"^(auto|[a-z]{2,3})$", description="Sprache für Whisper: ISO-Code oder auto"
    )
    use_subtitles: bool = Field(False, description="Manuelle YouTube-Untertitel statt Whisper verwenden")
    max_height: int = Field(1080, ge=360, le=2160, description="Maximale Downloadhöhe in Pixeln")
    sample_interval: float = Field(
        0.5, ge=0.1, le=5, description="Abstand der analysierten Frames in Sekunden"
    )
    threshold: float = Field(0.05, ge=0.001, le=0.9, description="Anteil geänderter Fläche für einen Wechsel")
    min_local_change: float = Field(
        0.004,
        ge=0.0005,
        le=0.5,
        description="Schwelle für kleine, zusammenhängende Änderungen (Flächenanteil)",
    )
    pixel_noise: int = Field(
        20, ge=1, le=128, description="Mindestdifferenz (0–255), ab der ein Pixel als geändert gilt"
    )
    stable_time: float = Field(1.0, ge=0, le=10, description="Ruhezeit vor dem Screenshot in Sekunden")
    max_wait: float = Field(8, ge=1, le=60, description="Danach Screenshot auch ohne Ruhe (Sekunden)")
    min_gap: float = Field(3.0, ge=0, le=120, description="Mindestabstand zwischen Screenshots in Sekunden")
    max_per_minute: int = Field(6, ge=1, le=60, description="Obergrenze der Screenshots pro Minute")
    analysis_width: int = Field(
        960, ge=320, le=1920, description="Breite des internen Vergleichsbildes in Pixeln"
    )
    masks: list[Mask] = Field(
        default_factory=list, description="Ignorierte Bereiche [x, y, w, h], relativ 0–1 (z. B. Webcam)"
    )
    timestamps: bool = Field(True, description="Absätze mit Zeitstempel [hh:mm:ss] beginnen")


PROFILES: dict[str, dict[str, float]] = {
    "slides": {"threshold": 0.08, "min_local_change": 0.02, "stable_time": 1.0, "min_gap": 5.0},
    "default": {"threshold": 0.05, "min_local_change": 0.004, "stable_time": 1.0, "min_gap": 3.0},
    "coding": {"threshold": 0.02, "min_local_change": 0.002, "stable_time": 1.5, "min_gap": 4.0},
}


class OptionsError(ValueError):
    """Ungültige Verarbeitungsparameter; ``errors`` bildet Schlüssel auf Meldung ab."""

    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))


def allowed(key: str) -> str:
    """Erlaubter Wertebereich eines Schlüssels als Text (für Fehlermeldungen)."""
    f = Options.model_json_schema()["properties"].get(key)
    if f is None:
        return "unbekannter Parameter"
    if "enum" in f:
        return ", ".join(f["enum"])
    if "minimum" in f:
        return f"{f['minimum']} bis {f['maximum']}"
    return {"masks": "Liste aus [x, y, w, h] mit Werten 0 bis 1", "lang": "ISO-Code oder auto"}.get(
        key, "true oder false" if f.get("type") == "boolean" else "Text"
    )


def env_options(env: Mapping[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in Options.model_fields:
        raw = env.get(f"STANDBILD_{name.upper()}")
        if raw:
            try:
                out[name] = json.loads(raw) if name == "masks" else raw
            except ValueError:
                out[name] = raw  # die Validierung meldet den Fehler
    return out


def effective_options(
    job: Mapping[str, Any] | None = None, env: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Wirksame Parameter. Rangfolge: Auftrag > Profil des Auftrags > Umgebung > eingebaut (Abschnitt 4)."""
    merged: dict[str, Any] = {}
    layers = (
        env_options(os.environ if env is None else env),
        {k: v for k, v in (job or {}).items() if v is not None},
    )
    for layer in layers:
        # Ein Profil setzt seine Schwellen; einzelne Werte derselben Ebene gehen vor.
        merged.update(PROFILES.get(str(layer.get("profile")), {}))
        merged.update(layer)
    try:
        return Options.model_validate(merged).model_dump()
    except ValidationError as exc:
        errors = {}
        for e in exc.errors():
            key = str(e["loc"][0]) if e["loc"] else "options"
            errors[key] = (
                "Unbekannter Parameter"
                if e["type"] == "extra_forbidden"
                else f"Ungültiger Wert – erlaubt: {allowed(key)}"
            )
        raise OptionsError(errors) from None
