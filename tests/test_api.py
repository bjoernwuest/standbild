"""REST-API (10.2) mit dem FastAPI-TestClient; die Pipeline ist eine Attrappe."""

import time
import zipfile
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path

import pytest
from conftest import fake_pipeline
from fastapi.testclient import TestClient
from test_models import CT2, hf, wait_for

from standbild.config import get_settings
from standbild.web.app import create_app

URL = "https://youtu.be/aaaaaaaaaaa"


@pytest.fixture
def client(db: str) -> Iterator[TestClient]:
    with TestClient(create_app(pipeline=fake_pipeline)) as c:
        yield c


def wait_done(client: TestClient, job_id: str) -> dict:
    end = time.time() + 15
    while (job := client.get(f"/api/jobs/{job_id}").json())["status"] not in ("done", "failed"):
        assert time.time() < end, job
        time.sleep(0.05)
    return job


def test_page_assets_health_and_docs(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200 and "YouTube-Link einfügen" in page.text and 'lang="de"' in page.text
    assert (
        "http://" not in page.text and "https://www.youtube.com/watch?v=…" in page.text
    )  # keine externen Assets
    assert (
        client.get("/static/app.js").status_code == 200 and client.get("/static/app.css").status_code == 200
    )
    health = client.get("/healthz").json()
    assert health["status"] == "ok" and health["checks"]["db"] is True and len(health["checks"]) == 4
    paths = client.get("/api/openapi.json").json()["paths"]
    assert {"/api/videos/lookup", "/api/defaults", "/api/jobs", "/api/jobs/{job_id}", "/api/jobs/{job_id}/package", "/api/models",
            "/api/models/{name}", "/api/models/{name}/default", "/healthz"} <= set(paths)  # fmt: skip
    assert client.get("/api/docs").status_code == 200


def test_defaults_list_ranges_profiles_and_models(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STANDBILD_THRESHOLD", "0.07")
    d = client.get("/api/defaults").json()
    assert d["defaults"]["threshold"] == 0.07 and d["defaults"]["whisper_model"] == "large-v3-turbo"
    assert d["profiles"]["coding"]["min_local_change"] == 0.002 and d["models"] == ["large-v3-turbo"]
    assert (d["fields"]["threshold"]["minimum"], d["fields"]["threshold"]["maximum"]) == (0.001, 0.9)
    assert (
        d["fields"]["profile"]["enum"] == ["slides", "default", "coding"]
        and "description" in d["fields"]["masks"]
    )


def test_lookup_states_from_unknown_to_done(client: TestClient) -> None:
    assert client.get("/api/videos/lookup", params={"url": "kein link"}).json()["state"] == "invalid"
    unknown = client.get("/api/videos/lookup", params={"url": URL + "?t=5"}).json()
    assert (unknown["state"], unknown["youtube_id"], unknown["title"], unknown["last_options"]) == (
        "unknown",
        "aaaaaaaaaaa",
        "Titel aaaaaaaaaaa",
        None,
    )

    created = client.post(
        "/api/jobs", json={"url": URL, "options": {"profile": "coding", "threshold": 0.03}}
    ).json()
    assert (
        created["status"] == "queued"
        and created["reused"] is False
        and created["youtube_id"] == "aaaaaaaaaaa"
    )
    assert created["options"]["threshold"] == 0.03 and created["options"]["min_local_change"] == 0.002
    job = wait_done(client, created["job_id"])
    assert (job["status"], job["progress"], job["screenshot_count"], job["error"]) == ("done", 1.0, 2, None)
    assert set(job) == {
        "job_id",
        "status",
        "stage",
        "progress",
        "title",
        "created_at",
        "finished_at",
        "error",
        "screenshot_count",
        "package_size",
    }

    done = client.get(
        "/api/videos/lookup", params={"url": "https://www.youtube.com/watch?v=aaaaaaaaaaa&list=x"}
    ).json()
    assert (done["state"], done["title"], done["channel"], done["duration"]) == (
        "done",
        "Titel aaaaaaaaaaa",
        "Testkanal",
        61.0,
    )
    assert (
        done["last_options"]["profile"] == "coding"
        and done["last_options_date"]
        and done["model_replaced"] is False
    )
    assert done["job"]["job_id"] == created["job_id"]


def test_reuse_force_and_changed_options(client: TestClient) -> None:
    first = client.post("/api/jobs", json={"url": URL}).json()
    wait_done(client, first["job_id"])
    same = client.post("/api/jobs", json={"url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"}).json()
    assert same["reused"] is True and same["job_id"] == first["job_id"] and same["status"] == "done"
    explicit_defaults = client.post(
        "/api/jobs", json={"url": URL, "options": {"threshold": 0.05, "profile": "default"}}
    ).json()
    assert explicit_defaults["job_id"] == first["job_id"]  # dieselben wirksamen Parameter
    changed = client.post("/api/jobs", json={"url": URL, "options": {"threshold": 0.2}}).json()
    assert changed["reused"] is False and changed["job_id"] != first["job_id"]
    wait_done(client, changed["job_id"])
    forced = client.post("/api/jobs", json={"url": URL, "force": True}).json()
    assert forced["reused"] is False and forced["job_id"] not in (first["job_id"], changed["job_id"])
    wait_done(client, forced["job_id"])
    recent = client.get("/api/jobs", params={"limit": 2}).json()
    assert [j["job_id"] for j in recent] == [forced["job_id"], changed["job_id"]] and recent[0][
        "has_package"
    ] is True


def test_validation_errors_name_the_field(client: TestClient) -> None:
    bad = client.post("/api/jobs", json={"url": URL, "options": {"threshold": 5, "gibtsnicht": 1}})
    assert bad.status_code == 422
    assert bad.json()["errors"] == {
        "threshold": "Ungültiger Wert – erlaubt: 0.001 bis 0.9",
        "gibtsnicht": "Unbekannter Parameter",
    }
    model = client.post("/api/jobs", json={"url": URL, "options": {"whisper_model": "medium"}})
    assert model.status_code == 422 and "large-v3-turbo" in model.json()["errors"]["whisper_model"]
    link = client.post("/api/jobs", json={"url": "https://vimeo.com/1"})
    assert link.status_code == 400 and "Kein gültiger YouTube-Link" in link.json()["detail"]
    assert client.get("/api/jobs").json() == []  # nichts davon hat einen Auftrag angelegt


def test_package_download_and_missing_package(client: TestClient) -> None:
    job = wait_done(client, client.post("/api/jobs", json={"url": URL}).json()["job_id"])
    res = client.get(f"/api/jobs/{job['job_id']}/package")
    assert res.status_code == 200 and res.headers["content-type"] == "application/zip"
    assert 'filename="aaaaaaaaaaa_' in res.headers["content-disposition"]
    assert "transcript.md" in zipfile.ZipFile(BytesIO(res.content)).namelist()
    assert client.get("/api/jobs/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.get("/api/jobs/unsinn/package").status_code == 404

    for package in get_settings().packages_dir.glob("*.zip"):  # Volume gelöscht
        package.unlink()
    assert client.get(f"/api/jobs/{job['job_id']}/package").status_code == 404
    assert client.get("/api/videos/lookup", params={"url": URL}).json()["state"] == "missing"
    again = client.post("/api/jobs", json={"url": URL}).json()  # ohne Paket wird nicht wiederverwendet
    assert again["reused"] is False and again["job_id"] != job["job_id"]


def test_failed_job_and_running_state(db: str) -> None:
    def broken(*args: object, **kw: object) -> None:
        time.sleep(0.3)
        raise RuntimeError("kaputt")

    with TestClient(create_app(pipeline=broken)) as client:
        job_id = client.post("/api/jobs", json={"url": URL}).json()["job_id"]
        second = client.post("/api/jobs", json={"url": URL, "force": True}).json()
        assert second["job_id"] == job_id and second["reused"] is True  # läuft schon: kein zweiter Auftrag
        assert client.get("/api/videos/lookup", params={"url": URL}).json()["state"] == "processing"
        assert client.get(f"/api/jobs/{job_id}/package").status_code == 409
        assert wait_done(client, job_id)["error"] == "RuntimeError: kaputt"
        failed = client.get("/api/videos/lookup", params={"url": URL}).json()
        assert failed["state"] == "failed" and failed["last_options"]["threshold"] == 0.05


def test_models_api_and_model_fallback(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    hf(monkeypatch, CT2)
    listed = client.get("/api/models").json()
    assert [(m["name"], m["default"], m["status"]) for m in listed] == [("large-v3-turbo", True, "ready")]
    bad = client.post("/api/models", json={"url": "nur-ein-wort"})
    assert bad.status_code == 400 and "Kein HuggingFace-Link" in bad.json()["detail"]
    added = client.post("/api/models", json={"url": "Systran/faster-whisper-small"})
    assert added.status_code == 202 and added.json() == {"name": "small", "status": "downloading"}
    wait_for(lambda: client.get("/api/defaults").json()["models"] == ["large-v3-turbo", "small"])

    job = wait_done(
        client,
        client.post("/api/jobs", json={"url": URL, "options": {"whisper_model": "small"}}).json()["job_id"],
    )
    assert job["status"] == "done"
    assert client.delete("/api/models/large-v3-turbo").status_code == 400  # Standardmodell
    assert client.put("/api/models/small/default").json() == {"name": "small", "status": "default"}
    assert client.put("/api/models/fehlt/default").status_code == 400
    assert client.delete("/api/models/large-v3-turbo").json() == {
        "name": "large-v3-turbo",
        "status": "removed",
    }
    assert client.put("/api/models/small/default").status_code == 200

    # Das zuletzt verwendete Modell ist nicht mehr installiert → Standardmodell und Hinweis
    hf(monkeypatch, CT2)
    client.post("/api/models", json={"url": "Systran/faster-whisper-tiny", "name": "t"})
    wait_for(lambda: "t" in client.get("/api/defaults").json()["models"])
    client.put("/api/models/t/default")
    client.delete("/api/models/small")
    look = client.get("/api/videos/lookup", params={"url": URL}).json()
    assert (
        look["model_replaced"] is True
        and look["last_options"]["whisper_model"] == "t"
        and look["options"]["whisper_model"] == "small"
    )


def test_token_protects_api_mcp_and_page(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import base64

    monkeypatch.setenv("STANDBILD_API_TOKEN", "geheim")
    get_settings.cache_clear()
    basic = "Basic " + base64.b64encode(b"irgendwer:geheim").decode()
    with TestClient(create_app(pipeline=fake_pipeline, run_workers=False)) as client:
        assert client.get("/healthz").status_code == 200  # Healthcheck bleibt offen
        api = client.get("/api/jobs")
        assert api.status_code == 401 and api.headers["www-authenticate"] == "Bearer"
        assert client.post("/mcp", json={}).status_code == 401
        page = client.get("/")
        assert page.status_code == 401 and page.headers["www-authenticate"].startswith("Basic")
        assert client.get("/api/jobs", headers={"Authorization": "Bearer falsch"}).status_code == 401
        assert client.get("/api/jobs", headers={"Authorization": "Basic kein-base64!"}).status_code == 401
        assert client.get("/api/jobs", headers={"Authorization": "Bearer geheim"}).json() == []
        assert client.get("/", headers={"Authorization": basic}).status_code == 200
        assert (
            client.get("/api/jobs", headers={"Authorization": basic}).status_code == 200
        )  # Browser nach der Anmeldung


def test_start_fails_clearly_when_a_volume_is_not_writable(
    db: str, env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (env / "blockiert").write_text("eine Datei, kein Verzeichnis")
    monkeypatch.setenv("STANDBILD_PACKAGES_DIR", str(env / "blockiert" / "packages"))
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="nicht beschreibbar.*Bind-Mount"):
        with TestClient(create_app(pipeline=fake_pipeline)):
            pass


def test_start_fails_on_invalid_parameter_environment(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STANDBILD_THRESHOLD", "5")
    with pytest.raises(ValueError, match="threshold: Ungültiger Wert – erlaubt: 0.001 bis 0.9"):
        with TestClient(create_app(pipeline=fake_pipeline)):
            pass
