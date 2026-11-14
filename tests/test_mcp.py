"""MCP-Tools (Abschnitt 11) über den In-Memory-Client des SDK und einmal über HTTP."""

import asyncio
import base64
import hashlib
import json
import zipfile
from io import BytesIO
from typing import Any

import pytest
from conftest import fake_pipeline
from mcp import Client

from standbild import worker
from standbild.config import get_settings
from standbild.db import repo
from standbild.mcp_server import build_mcp

URL = "https://www.youtube.com/watch?v=aaaaaaaaaaa"


def call(tool: str, **arguments: Any) -> Any:
    async def run() -> Any:
        async with Client(build_mcp()) as client:
            return await client.call_tool(tool, arguments)

    return asyncio.run(run())


def text(result: Any) -> str:
    return "\n".join(c.text for c in result.content if c.type == "text")


def finish_next() -> None:
    worker.process(repo.claim(), fake_pipeline)


def test_exactly_three_tools_with_documented_option_schema(db: str) -> None:
    async def run() -> Any:
        async with Client(build_mcp()) as client:
            return await client.list_tools()

    tools = {t.name: t for t in asyncio.run(run()).tools}
    assert set(tools) == {"process", "status", "get"}
    schema = json.dumps(tools["process"].input_schema)
    props = tools["process"].input_schema["$defs"]["Options"]["properties"]
    assert tools["process"].input_schema["required"] == ["url"]
    assert (props["threshold"]["minimum"], props["threshold"]["maximum"], props["threshold"]["default"]) == (
        0.001,
        0.9,
        0.05,
    )
    assert (
        props["profile"]["enum"] == ["slides", "default", "coding"]
        and "min_local_change" in schema
        and "masks" in schema
    )
    assert "15 Sekunden" in tools["status"].description


def test_process_status_get_round_trip(db: str) -> None:
    started = call("process", url=URL)
    assert not started.is_error
    job = started.structured_content
    assert (job["status"], job["reused"], job["youtube_id"], job["title"]) == (
        "queued",
        False,
        "aaaaaaaaaaa",
        "Titel aaaaaaaaaaa",
    )
    assert job["options"]["profile"] == "default" and job["options"]["whisper_model"] == "large-v3-turbo"
    assert json.loads(text(started))["job_id"] == job["job_id"]  # strukturiert und zusätzlich als Text

    assert call("status", job_id=job["job_id"]).structured_content["status"] == "queued"
    not_ready = call("get", job_id=job["job_id"])
    assert not_ready.is_error and "Status: queued" in text(not_ready)
    assert (
        call("process", url="https://youtu.be/aaaaaaaaaaa", force=True).structured_content["job_id"]
        == job["job_id"]
    )  # läuft schon

    finish_next()
    status = call("status", job_id=job["job_id"]).structured_content
    assert (status["status"], status["progress"], status["screenshot_count"]) == ("done", 1.0, 2) and status[
        "package_size"
    ] > 0

    got = call("get", job_id=job["job_id"])
    assert not got.is_error and [c.type for c in got.content] == ["text", "resource"]
    resource = got.content[1].resource
    assert (
        str(resource.uri) == f"standbild://packages/{job['job_id']}.zip"
        and resource.mime_type == "application/zip"
    )
    data = base64.b64decode(resource.blob)
    assert zipfile.ZipFile(BytesIO(data)).testzip() is None
    info = text(got)
    assert hashlib.sha256(data).hexdigest() in info and f"Größe: {len(data)} Bytes" in info
    assert f"http://localhost:8080/api/jobs/{job['job_id']}/package" in info and "Paket: aaaaaaaaaaa_" in info


def test_process_with_options_reuse_and_reprocess(db: str) -> None:
    options = {"profile": "coding", "threshold": 0.03, "masks": [[0.78, 0.72, 0.22, 0.28]]}
    first = call("process", url=URL, options=options).structured_content
    assert first["options"]["threshold"] == 0.03 and first["options"]["min_local_change"] == 0.002
    assert first["options"]["stable_time"] == 1.5 and first["options"]["masks"] == [[0.78, 0.72, 0.22, 0.28]]
    finish_next()
    again = call("process", url=URL, options=options).structured_content
    assert again["reused"] is True and again["job_id"] == first["job_id"] and again["status"] == "done"
    other = call("process", url=URL, options={**options, "threshold": 0.04}).structured_content
    assert (
        other["reused"] is False and other["job_id"] != first["job_id"]
    )  # abweichende Parameter: neu verarbeiten
    finish_next()
    forced = call("process", url=URL, options=options, force=True).structured_content
    assert forced["reused"] is False and forced["job_id"] != first["job_id"]


def test_tool_errors_are_precise(db: str) -> None:
    bad_link = call("process", url="https://example.com/video")
    assert bad_link.is_error and "Kein gültiger YouTube-Link" in text(bad_link)
    out_of_range = call("process", url=URL, options={"threshold": 7})
    assert out_of_range.is_error and "threshold" in text(out_of_range) and "0.9" in text(out_of_range)
    unknown_key = call("process", url=URL, options={"treshold": 0.1})
    assert unknown_key.is_error and "treshold" in text(unknown_key)
    unknown_model = call("process", url=URL, options={"whisper_model": "medium"})
    assert unknown_model.is_error and "Modell nicht installiert" in text(unknown_model)
    for tool in ("status", "get"):
        missing = call(tool, job_id="00000000-0000-0000-0000-000000000000")
        assert missing.is_error and text(missing).endswith("Verarbeitungs-ID nicht gefunden")
    assert repo.recent(10) == []


def test_get_does_not_embed_large_packages_and_reports_failures(
    db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = call("process", url=URL).structured_content
    finish_next()
    monkeypatch.setenv("STANDBILD_MCP_MAX_INLINE_MB", "0")
    monkeypatch.setenv("STANDBILD_PUBLIC_URL", "http://nas.local:8080/")
    get_settings.cache_clear()
    big = call("get", job_id=job["job_id"])
    assert not big.is_error and [c.type for c in big.content] == ["text"]
    assert "zu groß für eine Einbettung" in text(
        big
    ) and f"http://nas.local:8080/api/jobs/{job['job_id']}/package" in text(big)

    for package in get_settings().packages_dir.glob("*.zip"):
        package.unlink()
    gone = call("get", job_id=job["job_id"])
    assert gone.is_error and "Paket fehlt" in text(gone)

    def broken(*args: object, **kw: object) -> None:
        raise RuntimeError("kaputt")

    failed = call("process", url="https://youtu.be/bbbbbbbbbbb").structured_content
    worker.process(repo.claim(), broken)
    result = call("get", job_id=failed["job_id"])
    assert result.is_error and "Status: failed" in text(result) and "kaputt" in text(result)


def test_streamable_http_endpoint_with_bearer_token(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Der eingehängte Endpunkt /mcp spricht Streamable HTTP, zustandslos, hinter dem Bearer-Token."""
    from fastapi.testclient import TestClient

    from standbild.web.app import create_app

    monkeypatch.setenv("STANDBILD_API_TOKEN", "geheim")
    get_settings.cache_clear()
    headers = {
        "Authorization": "Bearer geheim",
        "Accept": "application/json, text/event-stream",
        "Host": "nas.local:8080",
    }
    with TestClient(create_app(pipeline=fake_pipeline, run_workers=False)) as client:
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}  # fmt: skip
        res = client.post("/mcp", json=init, headers=headers)
        assert res.status_code == 200, res.text
        assert res.json()["result"]["serverInfo"]["name"] == "standbild"
        assert "mcp-session-id" not in res.headers  # zustandslos
        listed = client.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=headers
        ).json()
        assert sorted(t["name"] for t in listed["result"]["tools"]) == ["get", "process", "status"]
        call_req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "process", "arguments": {"url": URL}},
        }
        called = client.post("/mcp", json=call_req, headers=headers).json()["result"]
        assert (
            called["structuredContent"]["status"] == "queued"
            and called["structuredContent"]["youtube_id"] == "aaaaaaaaaaa"
        )
