# standbild

**Erklärvideos zu Transkript + Screenshots.** standbild wandelt ein Software-Erklärvideo (YouTube-Link oder
lokale Datei) in ein ZIP-Paket um: ein Markdown-Transkript, in das an jeder relevanten Bildänderung ein
Screenshot in Originalauflösung eingehängt ist. Das Paket ist für Menschen lesbar und für eine spätere
Auswertung durch eine KI vorbereitet.

- Läuft **vollständig lokal** in einem einzigen Docker-Image (Whisper `large-v3-turbo` ist enthalten).
- **Keine „große KI“:** nur lokales Speech-to-Text (Whisper). Bildvergleich, Ausrichtung und Ausgabe sind
  klassische, deterministische Algorithmen. Kein OCR, keine Zusammenfassung.
- **Drei Zugänge, ein Kern:** Web-UI, MCP-Endpunkt und CLI; Aufträge liegen in einer Postgres-Warteschlange.

```markdown
>>>>> ![frame_0002 · 00:00:41](frames/frame_0002_00-00-41.png)

[00:00:41](https://www.youtube.com/watch?v=abc123&t=41s) Hier seht ihr die Datei docker-compose.yml. …
```

> **Hinweis zu YouTube:** Das Herunterladen von YouTube-Videos widerspricht den YouTube-Nutzungsbedingungen.
> standbild ist für den privaten Gebrauch gedacht. Verarbeite nur Videos, die du verwenden darfst.

## Das Paket

```
abc123_20261114-1830.zip
├── AGENTS.md              Anleitung für eine KI: wie das Paket zu lesen ist
├── transcript.md          Transkript mit >>>>>-Bildverweisen
├── frames/                Screenshots als PNG, exakt in der Auflösung des Videos
├── meta.json              Video-Metadaten, Kapitel, wirksame Parameter, Modell
└── log/
    ├── processing.log     Versionen, Einstellungen, Dauer jeder Stufe, Warnungen
    ├── frames.json        pro Screenshot: Zeit, Messwerte, Grund, Duplikat
    └── detection_log.csv  Messwerte pro analysiertem Frame
```

Eine Zeile `>>>>> ![frame_NNNN · hh:mm:ss](frames/…)` bedeutet: Ab hier zeigt der Bildschirm dieses Bild, bis
zur nächsten Markierung. Kehrt das Video zu einer früheren Ansicht zurück, verweist die Markierung auf das
frühere Bild. Markdown-Viewer wie Obsidian, VS Code oder GitHub zeigen die Bilder direkt an.

## Bauen und Bereitstellen

```bash
make build REGISTRY=registry.local:5000     # baut :<Version aus pyproject.toml> und :latest
make push  REGISTRY=registry.local:5000
```

Das entspricht `docker build -t registry.local:5000/standbild:1.0.0 -t registry.local:5000/standbild:latest .`
und zwei `docker push`. Der erste Build lädt das Whisper-Modell (ca. 1,6 GB); das CPU-Image ist ca. 3,4 GB groß.

| Build-Argument | Voreinstellung | Bedeutung |
| --- | --- | --- |
| `SEED_MODEL` | `large-v3-turbo` | Whisper-Modell der Startausstattung (Name wie in faster-whisper) |
| `YTDLP_VERSION` | leer | yt-dlp-Version; leer = neueste beim Build |
| `CUDA` | `0` | `1` installiert zusätzlich die CUDA-Bibliotheken für GPU-Betrieb |

Beispiel: `make build BUILD_ARGS="--build-arg CUDA=1"`.

## Starten

Die Postgres-Datenbank (Version ≥ 13) muss existieren und darf leer sein; die Tabellen legt standbild beim
Start selbst an. Der Datenbankbenutzer muss im Schema Tabellen anlegen dürfen. Ab Postgres 15 ist das im
Schema `public` nur noch dem Eigentümer der Datenbank erlaubt; fehlt das Recht, nennt die Fehlermeldung beim
Start den passenden `GRANT`.

```bash
docker run -d --name standbild --restart unless-stopped -p 8080:8080 \
  -v standbild-tmp:/tmp -v standbild-packages:/data/packages -v standbild-models:/models \
  -e STANDBILD_DB_HOST=db.local -e STANDBILD_DB_NAME=standbild \
  -e STANDBILD_DB_USER=standbild -e STANDBILD_DB_PASSWORD=geheim \
  -e STANDBILD_PUBLIC_URL=http://nas.local:8080 \
  registry.local:5000/standbild:latest
```

Danach: Web-UI unter `http://<host>:8080/`, REST unter `/api` (Dokumentation: `/api/docs`), MCP unter `/mcp`.
Beim ersten Start wird `large-v3-turbo` ohne Netzwerkzugriff ins Modell-Volume kopiert und als Standardmodell
eingetragen. GPU-Variante: Image mit `CUDA=1` bauen und zusätzlich `--gpus all` angeben.
Zeitstempel in Paketnamen und Protokollen sind UTC; mit `-e TZ=Europe/Berlin` gilt die lokale Zeit.

### Volumes

| Volume | Inhalt | Größenordnung |
| --- | --- | --- |
| `/tmp` | Videodownload und Zwischendateien je Auftrag (`/tmp/standbild/<job_id>/`), nach Abschluss gelöscht | einige GB pro laufendem Auftrag |
| `/data/packages` | Ergebnis-ZIPs (`<youtube-id>_<yyyymmdd-hhmm>.zip`) | 5–50 MB pro Paket |
| `/models` | Whisper-Modelle, ein Unterverzeichnis pro Modell | 0,5–3 GB pro Modell |

Ohne `-v` legt Docker anonyme Volumes an. Für Pakete und Modelle sind benannte Volumes oder Bind-Mounts
sinnvoll, damit sie Updates des Containers überleben. Der Container läuft als UID/GID 1000; bei Bind-Mounts
muss das Verzeichnis diesem Benutzer gehören, sonst bricht der Start mit einer klaren Meldung ab.

## Konfiguration

### Betrieb

| Variable | Voreinstellung | Bedeutung |
| --- | --- | --- |
| `STANDBILD_DB_HOST` | – (Pflicht) | Postgres-Host |
| `STANDBILD_DB_PORT` | `5432` | Postgres-Port |
| `STANDBILD_DB_NAME` | – (Pflicht) | Datenbankname |
| `STANDBILD_DB_USER` | – (Pflicht) | Benutzername |
| `STANDBILD_DB_PASSWORD` | – (Pflicht) | Passwort; alternativ `STANDBILD_DB_PASSWORD_FILE` (Docker Secrets) |
| `STANDBILD_DB_SCHEMA` | `public` | Schema der Tabellen; wird bei Bedarf angelegt |
| `STANDBILD_DB_SSLMODE` | `prefer` | libpq-SSL-Modus |
| `STANDBILD_PORT` | `8080` | HTTP-Port für Web-UI, API und MCP |
| `STANDBILD_PUBLIC_URL` | `http://localhost:8080` | Basis-URL für Download-Links in MCP-Antworten |
| `STANDBILD_WORKERS` | `1` | Anzahl paralleler Aufträge |
| `STANDBILD_API_TOKEN` | leer | Wenn gesetzt: Bearer-Token für `/api` und `/mcp`; Web-UI per Basic-Auth (beliebiger Benutzername, Passwort = Token) |
| `STANDBILD_DEVICE` | `auto` | `cpu` oder `cuda` |
| `STANDBILD_KEEP_RESULTS` | `3` | Gespeicherte Ergebnisse pro Video; ältere werden samt ZIP gelöscht |
| `STANDBILD_MCP_MAX_INLINE_MB` | `25` | Größte ZIP-Größe, die das MCP-Tool `get` direkt einbettet |
| `STANDBILD_TEMPLATE_DIR` | leer | Verzeichnis mit einer eigenen `AGENTS.md.j2` |
| `STANDBILD_YTDLP_AUTOUPDATE` | `false` | yt-dlp beim Start per pip aktualisieren |
| `STANDBILD_CONDITION_ON_PREVIOUS_TEXT` | `true` | Whisper-Kontext über Fenstergrenzen (siehe DECISIONS.md) |
| `STANDBILD_LOG_LEVEL` | `INFO` | Logausgabe auf stdout |
| `HF_TOKEN` | leer | Optional, für zugangsbeschränkte HuggingFace-Modelle |

### Verarbeitungsparameter

Jeder Parameter lässt sich pro Auftrag setzen (Web-UI „Parameter“, MCP `process` → `options`, CLI-Option
`--<name>`) und hat `STANDBILD_<NAME>` als Voreinstellung. Rangfolge, der höhere Rang gewinnt:

1. Werte des Auftrags
2. das beim Auftrag gewählte Profil
3. Umgebungsvariablen
4. eingebaute Voreinstellungen

Ungültige Werte werden abgelehnt, bevor ein Auftrag entsteht. Die wirksamen Werte stehen in `meta.json`.

| Schlüssel | Voreinstellung | Erlaubt | Bedeutung |
| --- | --- | --- | --- |
| `profile` | `default` | `slides`, `default`, `coding` | Satz von Schwellen (siehe unten) |
| `whisper_model` | Standardmodell | installierte Modelle | Modell für die Transkription |
| `lang` | `auto` | ISO-Code oder `auto` | Sprache für Whisper |
| `use_subtitles` | `false` | bool | Manuelle YouTube-Untertitel statt Whisper (automatische werden nie verwendet) |
| `max_height` | `1080` | 360–2160 | Maximale Downloadhöhe in Pixeln |
| `sample_interval` | `0.5` | 0,1–5 s | Abstand der analysierten Frames |
| `threshold` | `0.05` | 0,001–0,9 | Anteil geänderter Fläche für einen Wechsel |
| `min_local_change` | `0.004` | 0,0005–0,5 | Schwelle für kleine, zusammenhängende Änderungen |
| `pixel_noise` | `20` | 1–128 | Mindestdifferenz, ab der ein Pixel als geändert gilt |
| `stable_time` | `1.0` | 0–10 s | Ruhezeit vor dem Screenshot |
| `max_wait` | `8` | 1–60 s | Danach Screenshot auch ohne Ruhe |
| `min_gap` | `3.0` | 0–120 s | Mindestabstand zwischen Screenshots |
| `max_per_minute` | `6` | 1–60 | Obergrenze pro Minute |
| `analysis_width` | `960` | 320–1920 px | Breite des internen Vergleichsbildes |
| `masks` | leer | Liste `[x, y, w, h]`, relativ 0–1 | Bereiche, die ignoriert werden (z. B. Webcam) |
| `timestamps` | `true` | bool | Absätze beginnen mit `[hh:mm:ss]` |

| Profil | threshold | min_local_change | stable_time | min_gap |
| --- | --- | --- | --- | --- |
| `slides` | 0,08 | 0,02 | 1,0 s | 5 s |
| `default` | 0,05 | 0,004 | 1,0 s | 3 s |
| `coding` | 0,02 | 0,002 | 1,5 s | 4 s |

## Web-UI

Eine Seite unter `/`: YouTube-Link einfügen, dann zeigt eine Karte je nach Zustand **Verarbeiten**, den
Fortschritt, oder **Herunterladen** und **Erneut verarbeiten**. Der aufklappbare Bereich „Parameter“ ist mit
den zuletzt für dieses Video verwendeten Werten vorbelegt, sonst mit den Voreinstellungen. Darunter stehen die
letzten 20 Aufträge. `/?url=<Link>` öffnet die Karte zu einem Link direkt.

## MCP

Streamable HTTP unter `/mcp`, zustandslos, Servername `standbild`. Einbindung:

```json
{"mcpServers": {"standbild": {"type": "http", "url": "http://nas.local:8080/mcp",
  "headers": {"Authorization": "Bearer <token>"}}}}
```

(Der `headers`-Eintrag entfällt ohne `STANDBILD_API_TOKEN`.)

| Tool | Parameter | Ergebnis |
| --- | --- | --- |
| `process` | `url`, `force` (false), `options` (Schlüssel der Tabelle oben) | `job_id`, `status`, `reused`, wirksame `options`. Ein Ergebnis mit denselben wirksamen Parametern wird wiederverwendet. |
| `status` | `job_id` | `status` (`queued`, `running`, `done`, `failed`), `stage`, `progress`; höchstens alle 15 s abfragen |
| `get` | `job_id` | ZIP als eingebettete Ressource plus Name, Größe, SHA-256 und Download-URL; über `STANDBILD_MCP_MAX_INLINE_MB` nur die URL |

```json
{"url": "https://youtu.be/abc123", "options": {"profile": "coding", "threshold": 0.03, "masks": [[0.78, 0.72, 0.22, 0.28]]}}
```

## REST-API

| Methode und Pfad | Zweck |
| --- | --- |
| `GET /api/videos/lookup?url=` | Link normalisieren, Zustand des Videos, zuletzt verwendete Parameter |
| `GET /api/defaults` | Wirksame Voreinstellungen, Profile, Wertebereiche, installierte Modelle |
| `POST /api/jobs` `{url, force?, options?}` | Auftrag anlegen → `{job_id, status, reused, …}` |
| `GET /api/jobs/{id}` | Status |
| `GET /api/jobs/{id}/package` | ZIP herunterladen |
| `GET /api/jobs?limit=20` | Letzte Aufträge |
| `GET /api/models`, `POST /api/models` `{url, name?}`, `DELETE /api/models/{name}`, `PUT /api/models/{name}/default` | Modelle verwalten |
| `GET /healthz` | Lebenszeichen inkl. Datenbank und beschreibbarer Volumes |

## CLI

```
standbild serve                       Web-UI + REST + MCP + Worker (Standard-Befehl des Images)
standbild run <URL|Datei> [Optionen]  ein Video direkt verarbeiten, ohne Datenbank
standbild migrate                     Tabellen anlegen/aktualisieren (macht serve auch beim Start)
standbild preview <ZIP|Job-ID>        Diagramm der Änderungswerte zum Einstellen der Schwellen
```

```bash
# Einzelaufruf ohne Datenbank; das ZIP landet im Paket-Volume
docker run --rm -v standbild-packages:/data/packages -v standbild-models:/models \
  registry.local:5000/standbild:latest standbild run "https://www.youtube.com/watch?v=…" --profile coding

# im laufenden Container, mit Maske für eine Webcam unten rechts
docker exec standbild standbild run "https://youtu.be/…" --mask 0.78,0.72,0.22,0.28 --no-timestamps
```

`run` nimmt alles an, was yt-dlp unterstützt, oder eine lokale Videodatei, außerdem `--out <Verzeichnis>`
und `--cookies cookies.txt` (für altersbeschränkte Videos). Eingabe- und Downloadfehler enden mit Exit-Code 2.

## Whisper-Modelle verwalten

Reiter „Modelle“ im Web-UI: Liste aller Modelle mit Quelle, Größe, Status und Standardmodell. Zum Hinzufügen
einen HuggingFace-Link oder eine Repo-ID eintragen, z. B. `Systran/faster-whisper-medium` (optional mit
`…/tree/<revision>`). Akzeptiert werden nur CTranslate2-Modelle für faster-whisper (`model.bin`, `config.json`,
`tokenizer.json` oder `vocabulary.*`); Transformers-Modelle werden nicht konvertiert. Der Download läuft im
Hintergrund, danach steht das Modell im Parameterbereich und für MCP `options.whisper_model` zur Auswahl. Das
Standardmodell und Modelle, die gerade ein Auftrag nutzt, lassen sich nicht entfernen.

Die Modelle liegen ohne Datenbank im Volume `/models`: ein Verzeichnis pro Modell mit `standbild-model.json`,
das Standardmodell steht in `default.txt`.

## Schwellen einstellen mit `preview`

Zu viele oder zu wenige Screenshots? `preview` zeichnet aus einem fertigen Paket `global_ratio` und
`largest_region` über die Zeit, die beiden Schwellen als gestrichelte Linien und jeden gespeicherten
Screenshot als senkrechte Linie:

```bash
docker exec standbild standbild preview <Job-ID> --out /data/packages/preview.png
standbild preview abc123_20261114-1830.zip
```

- Kurve liegt oft knapp über `min_local_change`, ohne dass sich Wichtiges ändert → Wert erhöhen (oder Profil
  `slides`).
- Neue Codezeilen bleiben unter der Linie → Profil `coding` oder `min_local_change` senken.
- Lange Strecken über der Schwelle ohne Screenshot → `max_per_minute` hat gebremst; erhöhen, wenn mehr
  Zwischenstände gewünscht sind.
- Eine Webcam hält die Kurven dauerhaft oben → mit `masks` ausblenden.

`log/frames.json` nennt zu jedem Screenshot den Grund (`first`, `global`, `local`, `forced`).

## yt-dlp aktuell halten

YouTube ändert regelmäßig Details, die neue yt-dlp-Versionen erfordern. Regulär: Image neu bauen (yt-dlp wird
dabei aktualisiert) und neu ausrollen. Optional aktualisiert `STANDBILD_YTDLP_AUTOUPDATE=true` yt-dlp bei
jedem Containerstart per pip; schlägt das fehl, gilt die mitgelieferte Version. Scheitert ein Download mit
einem typischen yt-dlp-Fehler, steht im Auftrag der Hinweis „yt-dlp evtl. veraltet – Image neu bauen“.

## Richtwerte zur Laufzeit

Gemessen im Container auf einer VM mit 8 CPU-Kernen ohne GPU (`large-v3-turbo`, `int8`), Voreinstellungen:

| Video | Auflösung | Download | Bildwechsel | Transkription | gesamt | Screenshots | Paket |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 4:31 min, Live-Demo | 1280×706 | 8 s | 24 s | 2:00 min | 2:08 min | 23 | 5,7 MB |
| 4:23 min, Folien | 1920×1080 | 8 s | 13 s | 2:17 min | 2:24 min | 6 | 3,6 MB |
| 15:43 min, Folien + Demo | 1920×1080 | 10 s | 54 s | 4:43 min | 4:54 min | 57 | 25 MB |

Bildwechsel-Erkennung und Transkription laufen parallel; die Transkription bestimmt die Dauer (etwa ein
Drittel bis die Hälfte der Videolänge). Whisper nutzt 4 Threads; mehr Kerne lassen sich mit `OMP_NUM_THREADS`
zuweisen. Mit GPU ist die Transkription um ein Vielfaches schneller (nicht gemessen). Speicherbedarf: ca. 2 GB
je Worker.

## Entwicklung

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"            # braucht ffmpeg im PATH
pytest                             # Datenbanktests starten Postgres per testcontainers (Docker nötig)
pytest -m slow                     # Transkription mit dem Modell tiny (lädt es beim ersten Lauf)
make lint                          # ruff + mypy --strict
```

`tests/data/jfk.wav` ist eine gemeinfreie Aufnahme (John F. Kennedy, 1961) aus den Beispielen von whisper.cpp.
Mit `STANDBILD_TEST_DB=postgresql://user:pass@host:5432/db` laufen die Datenbanktests gegen einen vorhandenen
Postgres (jeder Test in einem eigenen Schema). Abweichungen von der Spezifikation stehen in
[DECISIONS.md](DECISIONS.md).

## Lizenz

[MIT](LICENSE)
