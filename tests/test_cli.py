"""CLI (4.1): run ohne Datenbank, preview, migrate und die Exit-Codes."""

import re
import zipfile
from pathlib import Path

import pytest
from test_detect import hold, slide, write_video
from typer.testing import CliRunner

from standbild.cli import app

runner = CliRunner()


def test_run_local_file_then_preview(tmp_path: Path) -> None:
    video = write_video(tmp_path / "demo.mp4", hold(slide(0), 4) + hold(slide(1), 4))
    out = tmp_path / "out"
    args = [
        "run",
        str(video),
        "--out",
        str(out),
        "--profile",
        "slides",
        "--mask",
        "0.8,0.8,0.2,0.2",
        "--no-timestamps",
    ]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    [package] = out.glob("demo_*.zip")
    assert f"{package}  (2 Screenshots)" in result.output
    with zipfile.ZipFile(package) as z:
        meta = z.read("meta.json").decode()
        assert '"profile": "slides"' in meta and '"threshold": 0.08' in meta and '"timestamps": false' in meta
        assert re.search(r'"masks": \[\s*\[\s*0.8,', meta) and '"whisper_model": "large-v3-turbo"' in meta
    png = tmp_path / "preview.png"
    shown = runner.invoke(app, ["preview", str(package), "--out", str(png)])
    assert shown.exit_code == 0, shown.output
    assert png.read_bytes().startswith(b"\x89PNG") and png.stat().st_size > 10_000


def test_exit_codes(tmp_path: Path) -> None:
    missing = runner.invoke(app, ["run", str(tmp_path / "fehlt.mp4")])
    assert missing.exit_code == 2 and "Datei nicht gefunden" in missing.output  # Eingabefehler: Exit-Code 2
    bad = runner.invoke(app, ["run", "x.mp4", "--threshold", "5"])
    assert bad.exit_code == 1 and "threshold" in bad.output and "0.001 bis 0.9" in bad.output
    bad_mask = runner.invoke(app, ["run", "x.mp4", "--mask", "a,b"])
    assert bad_mask.exit_code == 1
    model = runner.invoke(app, ["run", "x.mp4", "--whisper-model", "medium"])
    assert model.exit_code == 1 and "nicht installiert" in model.output


def test_migrate_and_preview_by_job_id(db: str) -> None:
    result = runner.invoke(app, ["migrate"])
    assert result.exit_code == 0 and "Schema ist aktuell" in result.output
    unknown = runner.invoke(app, ["preview", "00000000-0000-0000-0000-000000000000"])
    assert unknown.exit_code == 1 and "Weder ZIP-Datei noch fertige Verarbeitungs-ID" in unknown.output


def test_healthcheck_against_running_server(db: str, env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Docker-HEALTHCHECK: Exit-Code 0 bei 200, 1 bei 503 (mit JSON der Prüfungen), 1 ohne laufenden Dienst."""
    import json
    import socket
    import threading
    import time

    import uvicorn
    from conftest import fake_pipeline

    from standbild.config import get_settings
    from standbild.web.app import create_app

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setenv("STANDBILD_PORT", str(port))
    get_settings.cache_clear()
    server = uvicorn.Server(uvicorn.Config(create_app(pipeline=fake_pipeline), port=port, log_config=None))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        end = time.time() + 20
        while not server.started:
            assert time.time() < end and thread.is_alive(), "Server startet nicht"
            time.sleep(0.05)
        healthy = runner.invoke(app, ["healthcheck"])
        assert healthy.exit_code == 0, healthy.output
        assert json.loads(healthy.output)["status"] == "ok"
        (env / "models" / "default.txt").unlink()
        sick = runner.invoke(app, ["healthcheck"])
        assert sick.exit_code == 1 and json.loads(sick.output)["checks"]["model"] is False
    finally:
        server.should_exit = True
        thread.join(10)
    down = runner.invoke(app, ["healthcheck"])
    assert down.exit_code == 1 and f"127.0.0.1:{port}/healthz nicht erreichbar" in down.output
