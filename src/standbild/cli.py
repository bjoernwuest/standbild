"""Befehle: serve, run, migrate, preview (Abschnitt 4.1)."""

from __future__ import annotations

import logging
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Annotated

import typer

from standbild import models
from standbild.config import Options, OptionsError, effective_options, get_settings

app = typer.Typer(
    add_completion=False, no_args_is_help=True, help="Erklärvideos zu Transkript + Screenshots."
)


@app.callback()
def _setup() -> None:
    logging.basicConfig(
        level=get_settings().log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )


def _fail(message: str, code: int = 1) -> typer.Exit:
    typer.echo(f"Fehler: {message}", err=True)
    return typer.Exit(code)


def _update_ytdlp(target: Path) -> None:
    """12.5: yt-dlp beim Start per pip in ein beschreibbares Verzeichnis aktualisieren (vor dem ersten Import)."""
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--quiet",
        "--upgrade",
        "--target",
        str(target),
        "yt-dlp[default]",
    ]
    try:
        subprocess.run(cmd, check=True, timeout=300)
        sys.path.insert(0, str(target))
        logging.getLogger(__name__).info("yt-dlp aktualisiert in %s", target)
    except (subprocess.SubprocessError, OSError) as exc:
        logging.getLogger(__name__).warning(
            "yt-dlp-Aktualisierung fehlgeschlagen – mitgelieferte Version bleibt: %s", exc
        )


@app.command()
def serve() -> None:
    """Web-UI + REST + MCP + Worker (Standard-Befehl des Images)."""
    import uvicorn

    from standbild.web.app import create_app

    s = get_settings()
    if s.ytdlp_autoupdate:
        _update_ytdlp(s.tmp_dir / "yt-dlp")
    uvicorn.run(create_app(), host="0.0.0.0", port=s.port, log_level=s.log_level.lower())


@app.command()
def migrate() -> None:
    """Tabellen anlegen/aktualisieren (macht serve auch beim Start)."""
    from standbild.db.pool import migrate as run_migrations

    applied = run_migrations()
    typer.echo(f"Angewendete Migrationen: {applied or 'keine (Schema ist aktuell)'}")


@app.command()
def run(
    source: Annotated[str, typer.Argument(help="URL (alles, was yt-dlp unterstützt) oder lokale Videodatei")],
    out: Annotated[
        Path | None, typer.Option(help="Ausgabeverzeichnis für das ZIP (Voreinstellung: Paket-Volume)")
    ] = None,
    cookies: Annotated[Path | None, typer.Option(help="cookies.txt für yt-dlp")] = None,
    profile: Annotated[str | None, typer.Option()] = None,
    whisper_model: Annotated[str | None, typer.Option()] = None,
    lang: Annotated[str | None, typer.Option()] = None,
    use_subtitles: Annotated[bool | None, typer.Option("--use-subtitles/--no-use-subtitles")] = None,
    max_height: Annotated[int | None, typer.Option()] = None,
    sample_interval: Annotated[float | None, typer.Option()] = None,
    threshold: Annotated[float | None, typer.Option()] = None,
    min_local_change: Annotated[float | None, typer.Option()] = None,
    pixel_noise: Annotated[int | None, typer.Option()] = None,
    stable_time: Annotated[float | None, typer.Option()] = None,
    max_wait: Annotated[float | None, typer.Option()] = None,
    min_gap: Annotated[float | None, typer.Option()] = None,
    max_per_minute: Annotated[int | None, typer.Option()] = None,
    analysis_width: Annotated[int | None, typer.Option()] = None,
    mask: Annotated[list[str] | None, typer.Option(help="x,y,w,h relativ 0–1; mehrfach möglich")] = None,
    timestamps: Annotated[bool | None, typer.Option("--timestamps/--no-timestamps")] = None,
) -> None:
    """Ein Video direkt verarbeiten (ohne Datenbank), ZIP ins Ausgabeverzeichnis."""
    from standbild.pipeline import run_pipeline
    from standbild.pipeline.fetch import FetchError

    s = get_settings()
    job = {k: v for k, v in locals().items() if k in Options.model_fields and v is not None}
    try:
        if mask:
            job["masks"] = [[float(x) for x in m.split(",")] for m in mask]
        opts = effective_options(job)
    except (OptionsError, ValueError) as exc:
        raise _fail(str(exc)) from None
    models.seed()
    opts["whisper_model"] = opts["whisper_model"] or models.default_model()
    if not opts["use_subtitles"] and opts["whisper_model"] not in models.installed():
        raise _fail(
            f"Whisper-Modell nicht installiert: {opts['whisper_model']} (vorhanden: {', '.join(models.installed())})"
        )
    try:
        result = run_pipeline(
            source,
            opts,
            workdir=s.tmp_dir / f"run-{uuid.uuid4().hex[:8]}",
            out_dir=out or s.packages_dir,
            model_dir=models.path(str(opts["whisper_model"])),
            device=s.device,
            condition_on_previous_text=s.condition_on_previous_text,
            template_dir=s.template_dir,
            cookies=cookies,
        )
    except FetchError as exc:
        raise _fail(str(exc), 2) from None
    typer.echo(f"{result.package}  ({result.screenshots} Screenshots)")


@app.command()
def preview(
    target: Annotated[str, typer.Argument(help="ZIP-Datei oder Verarbeitungs-ID")],
    out: Annotated[Path | None, typer.Option(help="Zieldatei (PNG)")] = None,
) -> None:
    """Diagramm der Änderungswerte zum Einstellen der Schwellen."""
    from standbild.preview import render

    package = Path(target)
    if not package.is_file():
        from standbild.db import repo

        job = repo.get(target)
        if not job or not job["package_file"]:
            raise _fail(f"Weder ZIP-Datei noch fertige Verarbeitungs-ID: {target}")
        package = get_settings().packages_dir / job["package_file"]
    typer.echo(render(package, out or Path(package.stem + "_preview.png")))
