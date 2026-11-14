"""MCP-Endpunkt (Abschnitt 11): genau drei Tools – process, status, get."""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import BlobResourceContents, ContentBlock, EmbeddedResource, TextContent

from standbild import __version__, worker
from standbild.config import Options, get_settings
from standbild.db import repo


def build_mcp() -> MCPServer:
    mcp = MCPServer(
        "standbild",
        version=__version__,
        instructions=(
            "Wandelt ein Software-Erklärvideo von YouTube in ein ZIP-Paket aus Transkript und Screenshots um. "
            "Ablauf: process(url) → status(job_id) abfragen, bis status 'done' → get(job_id)."
        ),
    )

    @mcp.tool()
    def process(url: str, force: bool = False, options: Options | None = None) -> dict[str, Any]:
        """Verarbeitung eines YouTube-Videos starten.

        Gibt es schon ein Ergebnis mit denselben wirksamen Parametern, kommt dessen job_id zurück (reused: true);
        mit force=true oder abweichenden Parametern wird neu verarbeitet. In options nur die Schlüssel setzen, die
        von den Voreinstellungen abweichen sollen; die Antwort enthält alle wirksamen Parameter.
        """
        try:
            return worker.submit(url, options.model_dump(exclude_unset=True) if options else None, force)
        except ValueError as exc:  # ungültiger Link oder ungültige Parameter
            raise ToolError(str(exc)) from None

    @mcp.tool()
    def status(job_id: str) -> dict[str, Any]:
        """Stand einer Verarbeitung: status (queued, running, done, failed), stage und progress (0–1).

        Bitte höchstens alle 15 Sekunden abfragen.
        """
        job = repo.get(job_id)
        if job is None:
            raise ToolError("Verarbeitungs-ID nicht gefunden")
        return worker.view(job)

    @mcp.tool()
    def get(job_id: str) -> list[ContentBlock]:
        """Fertiges Paket abholen: das ZIP als eingebettete Ressource plus Name, Größe, SHA-256 und Download-URL.

        Zu große Pakete werden nicht eingebettet; dann enthält die Antwort nur die Download-URL.
        """
        s = get_settings()
        job = repo.get(job_id)
        if job is None:
            raise ToolError("Verarbeitungs-ID nicht gefunden")
        if job["status"] != "done":
            raise ToolError(
                f"Paket nicht verfügbar – Status: {job['status']}"
                + (f" ({job['error']})" if job["error"] else "")
            )
        path = worker.package_path(job)
        if path is None:
            raise ToolError("Paket fehlt im Volume – bitte mit process(force=true) erneut verarbeiten")
        data = path.read_bytes()
        info = (
            f"Paket: {path.name}\nGröße: {len(data)} Bytes\nSHA-256: {hashlib.sha256(data).hexdigest()}\n"
            f"Download: {s.public_url.rstrip('/')}/api/jobs/{job['id']}/package"
        )
        if len(data) > s.mcp_max_inline_mb * 1024 * 1024:
            note = f"\nDas Paket ist zu groß für eine Einbettung (Grenze {s.mcp_max_inline_mb} MB); bitte die Download-URL verwenden."
            return [TextContent(type="text", text=info + note)]
        resource = BlobResourceContents(
            uri=f"standbild://packages/{job['id']}.zip",
            mime_type="application/zip",
            blob=base64.b64encode(data).decode(),
        )
        return [TextContent(type="text", text=info), EmbeddedResource(type="resource", resource=resource)]

    return mcp
