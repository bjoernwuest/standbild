"""FastAPI: Web-UI, REST-API, Healthcheck und der eingehängte MCP-Endpunkt (Abschnitte 10 und 11)."""

from __future__ import annotations

import base64
import json
import logging
import secrets
import tempfile
import time
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from standbild import __version__, models, worker
from standbild.config import PROFILES, RECENT_JOBS, Options, OptionsError, effective_options, get_settings
from standbild.db import repo
from standbild.db.pool import close_pool, get_pool, migrate
from standbild.mcp_server import build_mcp
from standbild.pipeline import run_pipeline
from standbild.youtube import extract_id, video_info

log = logging.getLogger(__name__)
HERE = Path(__file__).parent


class TokenAuth:
    """Schützt alles außer /healthz: Bearer-Token für /api und /mcp, Basic-Auth (Passwort = Token) fürs Web-UI."""

    def __init__(self, app: ASGIApp, token: str):
        self.app, self.token = app, token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] == "/healthz":
            return await self.app(scope, receive, send)
        scheme, _, value = Headers(scope=scope).get("authorization", "").partition(" ")
        given = value.strip()
        if scheme.lower() == "basic":
            try:
                given = base64.b64decode(given).decode().partition(":")[2]
            except ValueError:
                given = ""
        if scheme.lower() in ("bearer", "basic") and secrets.compare_digest(given.encode(), self.token):
            return await self.app(scope, receive, send)
        # Die Seite fragt per Basic-Auth nach; der Browser schickt die Anmeldung danach auch an /api mit.
        challenge = "Bearer" if scope["path"].startswith(("/api", "/mcp")) else 'Basic realm="standbild"'
        await PlainTextResponse("Nicht angemeldet", 401, {"WWW-Authenticate": challenge})(
            scope, receive, send
        )


def writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=directory):
            return True
    except OSError:
        return False


def read_meta(package: Path) -> dict[str, Any]:
    """meta.json aus dem ZIP, ohne es zu entpacken."""
    try:
        with zipfile.ZipFile(package) as z:
            return json.loads(z.read("meta.json"))  # type: ignore[no-any-return]
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return {}


class JobRequest(BaseModel):
    url: str
    force: bool = False
    options: dict[str, Any] | None = None


class ModelRequest(BaseModel):
    url: str
    name: str | None = None


def create_app(pipeline: worker.Pipeline = run_pipeline, run_workers: bool = True) -> FastAPI:
    s = get_settings()
    volumes = (s.tmp_dir, s.packages_dir, s.models_dir)
    mcp = build_mcp()
    # Zustandslos mit JSON-Antworten; die Host-Prüfung des SDK ist nur für localhost gedacht und bleibt aus.
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        max_request_body_size=1024 * 1024,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    workers = worker.Workers(pipeline)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        for d in volumes:
            if not writable(d):
                raise RuntimeError(
                    f"Verzeichnis {d} ist nicht beschreibbar. Häufige Ursache: Bind-Mount mit anderem Besitzer "
                    "(der Container läuft als UID/GID 1000)."
                )
        effective_options()  # ungültige STANDBILD_<PARAMETER>-Werte sofort melden, nicht erst beim ersten Auftrag
        migrate()
        models.startup()
        for part in s.packages_dir.glob("*.zip.part"):  # Reste abgebrochener Paketierung
            if time.time() - part.stat().st_mtime > 3600:
                part.unlink(missing_ok=True)
        if run_workers:
            workers.start()
        log.info("standbild %s bereit – Modelle: %s", __version__, ", ".join(models.installed()) or "keine")
        async with mcp.session_manager.run():
            yield
        workers.shutdown()
        close_pool()

    app = FastAPI(
        title="standbild", version=__version__, lifespan=lifespan,
        docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None,
    )  # fmt: skip
    app.router.routes.extend(mcp_app.routes)  # /mcp direkt als Route: kein Redirect wie bei mount()
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    if s.api_token:
        app.add_middleware(TokenAuth, token=s.api_token)

    @app.exception_handler(OptionsError)
    def options_error(request: Request, exc: OptionsError) -> JSONResponse:
        return JSONResponse({"detail": str(exc), "errors": exc.errors}, 422)

    @app.exception_handler(ValueError)  # ungültiger Link, ModelError
    def value_error(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, 400)

    def job_or_404(job_id: str) -> repo.Job:
        job = repo.get(job_id)
        if job is None:
            raise HTTPException(404, "Verarbeitungs-ID nicht gefunden")
        return job

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index(request: Request) -> Any:
        return templates.TemplateResponse(request, "index.html", {"version": __version__})

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        """Healthcheck: Volumes beschreibbar, Tabelle jobs lesbar, Worker laufen, Standardmodell installiert.
        200 = gesund, 503 = mindestens eine Prüfung fehlgeschlagen. Ohne Anmeldung erreichbar."""
        checks = {str(d): writable(d) for d in volumes}
        try:
            with get_pool().connection(timeout=3) as conn:
                conn.execute("SELECT 1 FROM jobs LIMIT 1")  # Verbindung, Schema und Rechte in einem
            checks["db"] = True
        except Exception as exc:
            log.warning("Healthcheck: Datenbank nicht nutzbar: %s", str(exc).strip().splitlines()[0][:200])
            checks["db"] = False
        checks["workers"] = workers.alive()
        checks["model"] = models.default_model() is not None
        ok = all(checks.values())
        return JSONResponse({"status": "ok" if ok else "error", "checks": checks}, 200 if ok else 503)

    @app.get("/api/videos/lookup")
    def lookup(url: str) -> dict[str, Any]:
        """Link normalisieren und Zustand des Videos liefern (10.1)."""
        try:
            youtube_id = extract_id(url)
        except ValueError as exc:
            return {"state": "invalid", "error": str(exc)}
        out: dict[str, Any] = {
            "youtube_id": youtube_id,
            "thumbnail": f"https://i.ytimg.com/vi/{youtube_id}/mqdefault.jpg",
            "job": None,
            "last_options": None,
        }
        job = repo.latest(youtube_id)
        if job is None:
            return {**out, "state": "unknown", "title": (video_info(youtube_id) or {}).get("title")}
        package = worker.package_path(job)
        if job["status"] in ("queued", "running"):
            state = "processing"
        elif job["status"] == "failed":
            state = "failed"
        else:
            state = "done" if package else "missing"
        last = dict(job["options"])
        replaced = last.get("whisper_model") not in models.installed()
        if replaced:
            last["whisper_model"] = models.default_model()
        meta = read_meta(package) if package else {}
        return {
            **out,
            "state": state,
            "title": job["title"],
            "job": worker.view(job),
            "options": job["options"],
            "channel": meta.get("channel"),
            "duration": meta.get("duration"),
            "last_options": last,
            "last_options_date": job["created_at"].isoformat(),
            "model_replaced": replaced,
        }

    @app.get("/api/defaults")
    def defaults() -> dict[str, Any]:
        """Wirksame Voreinstellungen, Profile, Wertebereiche und installierte Modelle."""
        values = effective_options()
        values["whisper_model"] = values["whisper_model"] or models.default_model()
        return {
            "defaults": values,
            "profiles": PROFILES,
            "fields": Options.model_json_schema()["properties"],
            "models": models.installed(),
        }

    @app.post("/api/jobs")
    def create_job(body: JobRequest) -> dict[str, Any]:
        """Auftrag anlegen; Antwort enthält job_id, status und reused."""
        return worker.submit(body.url, body.options, body.force)

    @app.get("/api/jobs")
    def list_jobs(limit: int = RECENT_JOBS) -> list[dict[str, Any]]:
        return [
            {
                **worker.view(j),
                "youtube_id": j["youtube_id"],
                "has_package": worker.package_path(j) is not None,
            }
            for j in repo.recent(max(1, min(limit, 200)))
        ]

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str) -> dict[str, Any]:
        return worker.view(job_or_404(job_id))

    @app.get("/api/jobs/{job_id}/package")
    def download(job_id: str) -> FileResponse:
        job = job_or_404(job_id)
        package = worker.package_path(job)
        if package is None:
            raise HTTPException(
                409 if job["status"] != "done" else 404, f"Paket nicht verfügbar (Status: {job['status']})"
            )
        return FileResponse(package, media_type="application/zip", filename=package.name)

    @app.get("/api/models")
    def list_models() -> list[dict[str, Any]]:
        return models.list_models()

    @app.post("/api/models", status_code=202)
    def add_model(body: ModelRequest) -> dict[str, str]:
        """Modell von HuggingFace hinzufügen; der Download läuft im Hintergrund."""
        return {"name": models.add(body.url, body.name or None), "status": "downloading"}

    @app.delete("/api/models/{name}")
    def remove_model(name: str) -> dict[str, str]:
        models.remove(name, in_use=repo.model_in_use(name))
        return {"name": name, "status": "removed"}

    @app.put("/api/models/{name}/default")
    def set_default_model(name: str) -> dict[str, str]:
        models.set_default(name)
        return {"name": name, "status": "default"}

    return app
