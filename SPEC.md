# Spezifikation: standbild – Erklärvideos zu Transkript + Screenshots

Nov 14, 2026 · @Bjoern

## 1. Ziel und Leitprinzipien

**standbild** wandelt ein Software-Erklärvideo (YouTube-URL oder lokale Datei) in ein kompaktes Paket um: ein Markdown-Transkript, in das an jeder relevanten Bildänderung eine Markierung mit Bildverweis (`>>>>> ![…](frames/…)`) eingefügt ist, plus die zugehörigen Screenshots in voller Auflösung. Das Paket ist für Menschen lesbar und für eine spätere Auswertung durch eine KI vorbereitet.

Leitprinzipien:

- **100 % lokal lauffähig** als ein einziges Docker-Image. Alles Nötige ist enthalten, auch das Whisper-Modell `large-v3-turbo` als Startausstattung; nach `docker pull` genügen Umgebungsvariablen und drei Volumes.
- **Keine „große KI“.** Erlaubt ist ausschließlich ein lokales Speech-to-Text-Modell (Whisper). Bildvergleich, Ausrichtung und Ausgabe sind deterministische, klassische Algorithmen. Kein OCR.
- **Lesbarkeit vor Kompression:** Screenshots werden immer in Originalauflösung als PNG gespeichert. Verkleinerte Bilder dienen nur intern dem Vergleich.
- **Nachvollziehbar und einstellbar:** Jede Entscheidung (warum wurde hier ein Screenshot gemacht?) wird mit Messwerten protokolliert. Alle Schwellen haben Voreinstellungen, lassen sich per Umgebungsvariable ändern und pro Auftrag überschreiben.
- **Drei Zugänge, ein Kern:** Web-UI, MCP-Endpunkt und CLI nutzen dieselbe Pipeline und dieselbe Auftragsverwaltung in einer bestehenden (anfangs leeren) Postgres-Datenbank.
- **Das Paket ist das Endprodukt.** Es enthält Transkript, Screenshots, Protokoll und eine `AGENTS.md`, die einer KI erklärt, wie das Paket zu lesen ist. Die Zusammenfassung erfolgt später separat und ist nicht Aufgabe des Tools.

Nicht im Umfang: KI-Zusammenfassung, Sprecher-Erkennung (Diarization), Übersetzung, Videoschnitt, Benutzerverwaltung.

## 2. Bestehende Lösungen

Es gibt mehrere Open-Source-Tools mit ähnlichem Ansatz. Keines erfüllt alle Anforderungen zusammen (Inline-Markierungen im Fließtext, Stabilitätsprüfung, Docker, ohne LLM im Kern). Sie sind aber gute Referenzen und sollten vor der Umsetzung kurz getestet werden.

| Tool | Was es macht | Abweichung von dieser Spezifikation |
| --- | --- | --- |
| [slidegeist](https://pypi.org/project/slidegeist/) | Szenenerkennung per globaler Pixeldifferenz, Whisper, Tesseract-OCR, Markdown pro Folie | Gliedert nach Folien statt Inline-Markierung; Whisper über externen Dienst; optional lokales LLM für Bildbeschreibungen |
| [video-transcriber](https://pypi.org/project/video-transcriber/) | Visuell unterschiedliche Frames + Whisper, ZIP mit Markdown und Bildern | Am nächsten am Ziel; weniger Stellschrauben bei der Erkennung |
| [slidecap](https://pypi.org/project/slidecap/) | YouTube → folien-ausgerichtete Markdown-Notizen | Gliederung nach Folienabschnitten |
| [slidoc](https://github.com/shyenx/slidoc) | Keyframes per ffmpeg + whisper-cli, bereinigte Notizen | Bereinigung des Transkripts teils KI-gestützt |
| [vidwise](https://pypi.org/project/vidwise/) | Transkript + Key Frames + „Visual Guide“ für LLMs | Visual Guide wird mit KI erzeugt |

**Empfehlung:** Zuerst `video-transcriber` und `slidegeist` mit zwei typischen Videos testen. Reicht das Ergebnis nicht, diese Spezifikation umsetzen. Sie ist bewusst schlank (geschätzt 600–1.000 Zeilen Python inkl. Tests).

## 3. Architektur und Pipeline

Ein Container-Prozess (`standbild serve`) stellt Web-UI, REST-API und MCP-Endpunkt bereit und betreibt im selben Prozess einen oder mehrere Worker. Aufträge liegen als Warteschlange in Postgres; die Worker führen die Pipeline aus und legen das ZIP im Paket-Volume ab. Die Datenbank hält nur den Auftragsstatus und einen Verweis auf das Paket. Whisper-Modelle liegen in einem eigenen Volume.

**Zugänge** (Abschnitte 10, 11, 4):

- Web-UI unter `/`
- MCP-Endpunkt unter `/mcp` (Streamable HTTP)
- REST-API unter `/api` (wird vom Web-UI genutzt)
- CLI im Container (`docker exec` oder `docker run … standbild run`) für Tests und Einzelaufrufe

**Pipeline** eines Auftrags. Jede Stufe meldet Fortschritt in die Datenbank; Zwischendateien liegen in einem temporären Arbeitsverzeichnis, das nach Abschluss gelöscht wird:

1. **Download** → `source.mp4`, `audio.wav`, `meta.json`
2. **Transkription** (Whisper, lokal) → `transcript.json`
3. **Bildwechsel-Erkennung** (OpenCV, ohne KI) → `frames/*.png`, `frames.json`, `detection_log.csv`
4. **Zusammenführen** → `transcript.md`, `AGENTS.md`, `processing.log`, `package.zip`
5. **Ablegen** → ZIP ins Paket-Volume, Auftrag in Postgres auf `done` (Abschnitt 9)

Stufen 2 und 3 sind unabhängig und dürfen parallel laufen.

Technologie:

- Python 3.12
- `yt-dlp` für den Download, `ffmpeg` für Audio und Dekodierung
- `faster-whisper` (CTranslate2) für Speech-to-Text, `huggingface_hub` für das Herunterladen weiterer Modelle
- `opencv-python-headless`, `numpy`, `scikit-image` (SSIM), `imagehash` für den Bildvergleich
- `FastAPI` + `uvicorn` für Web-UI und REST, `Jinja2` für die HTML-Seite
- offizielles Python-MCP-SDK (`mcp`, FastMCP) für den MCP-Endpunkt, eingehängt in dieselbe FastAPI-App
- `psycopg` 3 (mit Connection-Pool) für Postgres
- `typer` für die CLI, `pydantic-settings` für die Konfiguration

## 4. CLI und Konfiguration

Betriebswerte kommen aus Umgebungsvariablen (`STANDBILD_*`). Für die Verarbeitungsparameter gilt diese Rangfolge, der höhere Rang gewinnt:

1. Werte, die beim Auftrag übergeben werden (Web-UI „Parameter“, MCP `process` → `options`, CLI-Optionen)
2. Das beim Auftrag gewählte Profil (`slides` / `default` / `coding`, Abschnitt 7.8)
3. Umgebungsvariablen (Tabelle 4.3)
4. Eingebaute Voreinstellungen

Die wirksamen Werte eines Auftrags werden in `jobs.options` und in `meta.json` gespeichert.

### 4.1 Befehle

```
standbild serve                       # Web-UI + REST + MCP + Worker (Standard-Befehl des Images)
standbild run <URL|Datei> [Optionen]  # ein Video direkt verarbeiten, ZIP ins Ausgabeverzeichnis
standbild migrate                     # Tabellen anlegen/aktualisieren (macht serve auch beim Start)
standbild preview <ZIP|Job-ID>        # Diagramm der Änderungswerte zum Einstellen der Schwellen
```

### 4.2 Umgebungsvariablen: Betrieb

| Variable | Voreinstellung | Bedeutung |
| --- | --- | --- |
| `STANDBILD_DB_HOST` | – (Pflicht) | Postgres-Host |
| `STANDBILD_DB_PORT` | `5432` | Postgres-Port |
| `STANDBILD_DB_NAME` | – (Pflicht) | Datenbankname |
| `STANDBILD_DB_USER` | – (Pflicht) | Benutzername |
| `STANDBILD_DB_PASSWORD` | – (Pflicht) | Passwort (alternativ `STANDBILD_DB_PASSWORD_FILE` für Docker Secrets) |
| `STANDBILD_DB_SCHEMA` | `public` | Schema, in dem die Tabellen angelegt werden |
| `STANDBILD_DB_SSLMODE` | `prefer` | libpq-SSL-Modus |
| `STANDBILD_PORT` | `8080` | HTTP-Port für Web-UI, API und MCP |
| `STANDBILD_PUBLIC_URL` | `http://localhost:8080` | Basis-URL für Download-Links in MCP-Antworten |
| `STANDBILD_WORKERS` | `1` | Anzahl paralleler Aufträge |
| `STANDBILD_API_TOKEN` | leer | Wenn gesetzt: Bearer-Token für `/api` und `/mcp`, Web-UI per Basic-Auth |
| `STANDBILD_DEVICE` | `auto` | `cpu` oder `cuda` |
| `STANDBILD_KEEP_RESULTS` | `3` | Gespeicherte Ergebnisse pro Video |
| `STANDBILD_MCP_MAX_INLINE_MB` | `25` | Größte ZIP-Größe, die `get` direkt einbettet |
| `HF_TOKEN` | leer | Optional, für zugangsbeschränkte HuggingFace-Modelle |
| `STANDBILD_LOG_LEVEL` | `INFO` | Logausgabe auf stdout |

Pfade sind feste Volumes (Abschnitt 12) und keine Variablen: `/tmp` (Arbeitsdateien), `/data/packages` (ZIP-Pakete), `/models` (Whisper-Modelle).

### 4.3 Verarbeitungsparameter

Jeder Parameter hat eine Umgebungsvariable `STANDBILD_<NAME>` als Voreinstellung und kann pro Auftrag unter seinem Schlüssel überschrieben werden. Ungültige Werte führen zu einer Fehlermeldung, bevor der Auftrag angelegt wird.

| Schlüssel | Voreinstellung | Erlaubt | Bedeutung |
| --- | --- | --- | --- |
| `profile` | `default` | `slides`, `default`, `coding` | Satz von Schwellen (7.8) |
| `whisper_model` | Standardmodell (10.3) | installierte Modelle | Modell für die Transkription |
| `lang` | `auto` | ISO-Code oder `auto` | Sprache für Whisper |
| `use_subtitles` | `false` | bool | Manuelle YouTube-Untertitel statt Whisper |
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

In der CLI heißen die Optionen entsprechend (`--threshold`, `--mask x,y,w,h` mehrfach usw.).

## 5. Stufe 1: Eingabe und Download

Eingabe ist eine URL (alles, was `yt-dlp` unterstützt) oder ein Pfad zu einer lokalen Videodatei.

Jeder Auftrag arbeitet in `/tmp/standbild/<job_id>/` (Volume `/tmp`). Das Verzeichnis wird nach Abschluss gelöscht, auch bei Fehlern. Beim Start räumt der Dienst verwaiste Verzeichnisse auf, die zu keinem laufenden Auftrag gehören.

- **Auflösung:** Das Video wird in der höchsten verfügbaren Auflösung bis `--max-height` geladen (Voreinstellung 1080p, 1440p/2160p möglich). Formatauswahl: `bv*[height<=H]+ba/b[height<=H]`, Zusammenführung zu MP4. Hintergrund: Kleiner Text in Software-Oberflächen ist bei 720p oft unleserlich.
- **Metadaten:** Titel, Kanal, Dauer, Upload-Datum, URL, Kapitelmarken (falls vorhanden) und Beschreibung werden in `meta.json` gespeichert.
- **Untertitel:** Manuell erstellte Untertitel in der Zielsprache werden zusätzlich als `subs.vtt` geladen. Automatisch erzeugte YouTube-Untertitel werden nicht verwendet.
- **Audio:** `ffmpeg` extrahiert `audio.wav` (16 kHz, mono) für Whisper.
- **Lokale Datei:** Wird nicht kopiert, sondern per Pfad referenziert. `meta.json` enthält dann Dateiname, Dauer und Auflösung (per `ffprobe`).
- **Fehler:** Nicht erreichbare URL, Altersbeschränkung oder fehlende Cookies führen zu einer klaren Fehlermeldung mit Exit-Code 2. Optional kann eine `cookies.txt` übergeben werden (`--cookies`).

Hinweis: Das Herunterladen von YouTube-Videos widerspricht den YouTube-Nutzungsbedingungen. Das Tool ist für den privaten Gebrauch gedacht. Die README muss darauf hinweisen.

## 6. Stufe 2: Transkription

`faster-whisper` läuft lokal und liefert Segmente mit Wort-Zeitstempeln (`word_timestamps=True`).

- **Modelle:** liegen im Volume `/models`, ein Unterverzeichnis pro Modell (CTranslate2-Format für faster-whisper). Das Image enthält `large-v3-turbo` als Startausstattung unter `/opt/standbild/seed-models`. Fehlt es beim Start in `/models`, wird es dorthin kopiert und als Standardmodell registriert. Weitere Modelle kommen über das Web-UI hinzu (10.3).
- **Auswahl:** pro Auftrag über `whisper_model`, sonst das im Web-UI gesetzte Standardmodell.
- **Rechenwerk:** `cuda` mit `float16`, wenn eine GPU vorhanden ist, sonst `cpu` mit `int8`. Jeder Worker hält das zuletzt benutzte Modell im Speicher und lädt nur bei einem Modellwechsel neu.
- **Halluzinationen eindämmen:** VAD-Filter aktivieren (`vad_filter=True`), `condition_on_previous_text=False`. Segmente, die sich mehr als dreimal wörtlich wiederholen, werden entfernt und im Protokoll vermerkt.
- **Fortschritt:** Anteil der bereits transkribierten Audiodauer wird laufend in die Datenbank geschrieben (höchstens alle 2 s).
- **Ausgabe `transcript.json`:** Liste von Segmenten `{id, start, end, text, words:[{start, end, word}]}` sowie die erkannte Sprache.
- **Alternative Quelle:** Mit `use_subtitles` und vorhandenen manuellen Untertiteln werden diese in dasselbe Format umgewandelt und Whisper übersprungen. Wort-Zeitstempel fehlen dann; die Ausrichtung arbeitet auf Segmentebene.

Richtwerte zur Laufzeit sind nicht Teil der Spezifikation, sollen aber im README nach ersten Tests ergänzt werden.

## 7. Stufe 3: Erkennung von Bildwechseln

Die Erkennung vergleicht jedes analysierte Frame mit dem **zuletzt gespeicherten Screenshot** (Referenz), wartet nach einem erkannten Wechsel, bis das Bild ruhig ist, und speichert dann das Frame in **Originalauflösung**. Verkleinerte Bilder werden nur für den Vergleich verwendet und nie ausgegeben.

### 7.1 Abtastung

- Das Video wird sequenziell dekodiert (OpenCV `grab()`/`retrieve()` oder ein `ffmpeg`-Pipe mit `fps=1/sample_interval`). Kein wahlfreies Springen, da das bei langen Videos langsam ist.
- Zu jedem analysierten Frame wird das Original im Speicher gehalten, bis klar ist, ob es gespeichert wird.

### 7.2 Vorverarbeitung (nur für den Vergleich)

1. Auf `analysis_width` skalieren (Voreinstellung 960 px, Seitenverhältnis bleibt). Das ist breit genug, damit eine neue Textzeile in einer IDE als zusammenhängende Fläche sichtbar bleibt.
2. In Graustufen umwandeln, Gauß-Weichzeichner 3×3 gegen Kompressionsrauschen.
3. Masken (`--mask`) auf 0 setzen, in Frame und Referenz gleichermaßen.
4. Fast schwarze oder einfarbige Frames (Mittelwert < 10 oder Standardabweichung < 3) werden als Überblendung markiert und nie gespeichert.

### 7.3 Messwerte pro Frame

- **Differenzmaske:** `absdiff(frame, referenz) > pixel_noise` (Voreinstellung 20 von 255), danach morphologisches Öffnen (3×3), um vereinzelte Pixel zu entfernen.
- **global\_ratio:** Anteil geänderter Pixel an allen nicht maskierten Pixeln. Entspricht dem „x %“ aus der Anforderung.
- **largest\_region:** Fläche der größten zusammenhängenden geänderten Region (Connected Components nach Dilatation 5×5), als Anteil am Bild. Erfasst kleine, aber zusammenhängende Änderungen.
- **motion:** global\_ratio zwischen aktuellem und vorherigem analysiertem Frame. Zeigt, ob das Bild gerade in Bewegung ist.

### 7.4 Auslöseregel

Ein Wechsel gilt als erkannt, wenn **eine** der Bedingungen zutrifft:

- `global_ratio ≥ threshold` (große Änderung, z. B. neue Folie), oder
- `largest_region ≥ min_local_change` (kleine, zusammenhängende Änderung, z. B. neue Codezeile, geöffneter Dialog).

`min_local_change` (Voreinstellung 0,4 %) liegt bewusst über der Fläche eines Mauszeigers (bei 960 px Breite ca. 0,1–0,15 %), damit Zeigerbewegungen nicht auslösen.

### 7.5 Zustandsautomat

```
RUHIG:
    wenn Auslöseregel erfüllt → t_detect = t; Zustand = WECHSELT
WECHSELT:
    wenn motion < motion_quiet (0,2 %) seit stable_time Sekunden:
        Kandidat = aktuelles Frame
        prüfe Auslöseregel erneut gegen Referenz
          erfüllt   → speichern (Grund: global | local)
          nicht mehr → verwerfen (z. B. Tooltip kurz eingeblendet)
        Zustand = RUHIG
    wenn seit t_detect > max_wait (8 s) keine Ruhe:
        speichern mit Grund "forced" (Scrollen, Videoeinspielung)
        Zustand = WECHSELT mit neuem t_detect
```

Zusätzliche Regeln:

- Das erste ruhige Frame des Videos wird immer gespeichert (Grund `first`).
- Zwischen zwei Screenshots liegen mindestens `min_gap` Sekunden. Bei mehr als `max_per_minute` Screenshots innerhalb von 60 s wird bis zum Ende des Fensters nur noch der jeweils letzte Kandidat behalten.
- Nach jedem Speichern wird der gespeicherte Screenshot zur neuen Referenz.

### 7.6 Duplikate

Kehrt das Video zu einer früheren Ansicht zurück, wird kein neues Bild gespeichert. Prüfung gegen alle bisherigen Screenshots: Perceptual Hash (pHash) mit Hamming-Abstand ≤ 4 **und** SSIM ≥ 0,98 auf dem Vergleichsbild. Treffer → Verweis auf die bestehende Datei.

### 7.7 Dateien und Protokoll

- **Dateiname:** `frame_0007_00-12-34.png` (laufende Nummer + Zeitstempel hh-mm-ss), PNG verlustfrei.
- **`frames.json`:** pro Screenshot `{file, t, t_detect, global_ratio, largest_region, reason, duplicate_of}`.
- **`detection_log.csv`:** eine Zeile pro analysiertem Frame mit allen Messwerten, Zustand und Aktion. Das ist die Grundlage zum Einstellen der Schwellen.
- **`standbild preview`:** zeichnet mit matplotlib ein PNG mit global\_ratio und largest\_region über die Zeit, den Schwellen als Linien und den Speicherzeitpunkten als Markierungen.

### 7.8 Profile

| Profil | threshold | min\_local\_change | stable\_time | min\_gap |
| --- | --- | --- | --- | --- |
| `slides` | 0,08 | 0,02 | 1,0 s | 5 s |
| `default` | 0,05 | 0,004 | 1,0 s | 3 s |
| `coding` | 0,02 | 0,002 | 1,5 s | 4 s |

## 8. Stufe 4: Zusammenführen und Ausgabeformat

Jede Markierung wird an der Satzgrenze eingefügt, die dem Beginn des Bildwechsels (`t_detect`) am nächsten liegt. So wird kein Satz zerrissen, und das Bild steht dort, wo der Sprecher darüber zu reden beginnt.

### 8.1 Einfügeregel

1. Satzgrenzen aus den Wort-Zeitstempeln bestimmen: Wortende mit `.`, `?`, `!` oder `:`, oder eine Sprechpause ≥ 0,8 s.
2. Für jeden Screenshot die Satzgrenze wählen, die `t_detect` am nächsten liegt, höchstens `align_tolerance` (4 s) entfernt.
3. Gibt es keine solche Grenze (sehr langer Satz), wird an der nächsten Wortgrenze eingefügt.
4. Fallen mehrere Screenshots auf dieselbe Grenze, erscheinen sie in zeitlicher Reihenfolge untereinander.
5. Screenshots in Pausen ohne Sprache (z. B. stumme Demo) erscheinen an ihrer zeitlichen Position zwischen den Absätzen.

### 8.2 Absätze

Ein neuer Absatz beginnt bei jeder Markierung und bei Sprechpausen ≥ 2 s. Jeder Absatz beginnt mit einem Zeitstempel `[hh:mm:ss]` (abschaltbar mit `--no-timestamps`). Bei YouTube-Quellen ist der Zeitstempel ein Link auf die Videostelle (`&t=754s`).

### 8.3 Format von `transcript.md`

```markdown
---
title: "Docker Compose in 20 Minuten"
source: https://www.youtube.com/watch?v=abc123
channel: "Beispielkanal"
duration: "00:21:40"
language: de
screenshots: 23
generated: 2026-11-14T18:30:00+01:00
tool: standbild 1.0.0 (whisper large-v3-turbo, profile default)
---

# Docker Compose in 20 Minuten

>>>>> ![frame_0001 · 00:00:03](frames/frame_0001_00-00-03.png)

[00:00:03] Hallo und willkommen. Heute schauen wir uns an, wie man mit Docker Compose mehrere Container gemeinsam startet.

>>>>> ![frame_0002 · 00:00:41](frames/frame_0002_00-00-41.png)

[00:00:41] Hier seht ihr die Datei docker-compose.yml. Ganz oben steht der Abschnitt services ...
```

Regeln:

- Eine Markierung steht allein in einer Zeile, mit Leerzeile davor und danach, exakt im Format `>>>>> ![<frame_NNNN> · <hh:mm:ss>](frames/<Dateiname>)`. Der Präfix `>>>>>` macht die Stelle für Menschen und Skripte leicht auffindbar; der Markdown-Bildverweis sorgt dafür, dass Viewer wie Obsidian, VS Code oder GitHub das Bild direkt anzeigen.
- Bei einem Duplikat (7.6) wird der Pfad des früheren Screenshots erneut verwendet; der Zeitstempel im Alt-Text ist der aktuelle.

### 8.4 Das Paket

Das Paket ist eine ZIP-Datei. Name: `<youtube-id>_<yyyymmdd-hhmm>.zip`. Alle Verweise darin sind relativ, das Paket ist in sich geschlossen.

```
abc123_20261114-1830.zip
├── AGENTS.md            # Anleitung für eine KI (8.5)
├── transcript.md        # Transkript mit >>>>>-Bildverweisen
├── frames/
│   ├── frame_0001_00-00-03.png
│   └── …
├── meta.json            # Video-Metadaten, Kapitel, wirksame Parameter, Modell
└── log/
    ├── processing.log   # Verarbeitungsprotokoll
    ├── frames.json      # pro Screenshot: Zeit, Messwerte, Grund, Duplikat
    └── detection_log.csv # Messwerte pro analysiertem Frame
```

`processing.log` enthält: Tool- und Bibliotheksversionen (standbild, yt-dlp, faster-whisper, Modell), alle wirksamen Einstellungen, Start- und Endzeit jeder Stufe, Dauer, erkannte Sprache, Anzahl Screenshots und Duplikate, entfernte Whisper-Wiederholungen sowie alle Warnungen.

Das Quellvideo und `audio.wav` sind nicht im Paket.

### 8.5 AGENTS.md

`AGENTS.md` wird aus einer Jinja2-Vorlage (`templates/AGENTS.md.j2`) mit den Werten des Videos erzeugt. Sie ist kurz (Ziel: unter 60 Zeilen) und richtet sich an eine KI, die das Paket später zusammenfassen oder auswerten soll. Sie gibt **keine** Zusammenfassungsaufgabe vor; die bringt der Nutzer selbst mit.

Inhalt:

1. **Kopf:** Titel, Kanal, URL, Dauer, Sprache, Verarbeitungsdatum, Anzahl Screenshots.
2. **Was das Paket ist:** ein Software-Erklärvideo, zerlegt in gesprochenen Text und Bildschirmzustände.
3. **Dateien:** eine Zeile pro Datei/Ordner, was sie enthält und ob sie gelesen werden sollte (Pflicht: `transcript.md` und `frames/`; bei Bedarf: `meta.json`; nur zur Fehlersuche: `log/`).
4. **Lesart der Markierung:** Eine Zeile `>>>>> ![…](frames/…)` bedeutet: Ab dieser Stelle zeigt der Bildschirm das verlinkte Bild, bis zur nächsten Markierung. `[hh:mm:ss]` ist die Position im Video. Verweist eine Markierung auf ein früheres Bild, kehrt das Video zu dieser Ansicht zurück.
5. **Hinweise zur Auswertung:**
   - Der Sprecher bezieht sich oft auf Sichtbares („hier“, „dieser Button“). Die Bedeutung ergibt sich aus dem Bild an der vorherigen Markierung.
   - Menüpfade, Code, Einstellungen und Werte stehen oft nur im Bild und werden nicht ausgesprochen. Die Bilder sind daher gleichwertig mit dem Text.
   - Das Transkript stammt von einer automatischen Spracherkennung. Fachbegriffe, Produktnamen und Befehle können falsch geschrieben sein; die Schreibweise im Bild hat Vorrang.
   - Die Screenshots sind automatisch ausgewählt. Kurze Zwischenzustände (Animationen, Scrollen) können fehlen.
   - Kapitelmarken aus `meta.json` können zur Gliederung genutzt werden.
6. **Empfohlenes Vorgehen:** `transcript.md` von oben nach unten lesen und an jeder Markierung das Bild ansehen, bevor der folgende Text interpretiert wird.

Die Vorlage liegt im Image und kann über ein eingehängtes Verzeichnis (`STANDBILD_TEMPLATE_DIR`) ersetzt werden.

## 9. Datenbank und Auftragsverwaltung

Die Postgres-Datenbank (Version ≥ 13) existiert bereits, ist aber leer. Die Anwendung legt ihre Tabellen selbst an; der Datenbankbenutzer hat die nötigen Rechte. Die Datenbank wird bewusst schlank gehalten: Sie dient nur als Auftragswarteschlange und Verzeichnis der Ergebnisse. Transkript, Screenshots und Metadaten stehen ausschließlich im ZIP im Volume `/data/packages`; Whisper-Modelle verwaltet das Tool direkt im Volume `/models` (10.3).

### 9.1 Video-Identität

Jede Eingabe wird auf die YouTube-Video-ID (11 Zeichen) normalisiert. Unterstützte Formen: `youtube.com/watch?v=`, `youtu.be/`, `youtube.com/shorts/`, `youtube.com/embed/`, `youtube.com/live/`, `m.youtube.com`, jeweils mit oder ohne weitere Parameter (`&t=`, `&list=`, `si=`). Ungültige Eingaben werden vor dem Anlegen eines Auftrags mit einer klaren Meldung abgelehnt. „Bereits vorhanden“ bedeutet: Zu dieser Video-ID gibt es ein erfolgreiches Ergebnis.

### 9.2 Schema

Beim Start von `serve` (und mit `standbild migrate`) wendet die Anwendung nummerierte SQL-Migrationen aus dem Image an: `CREATE SCHEMA IF NOT EXISTS` (falls nicht `public`), dann alle noch nicht angewendeten Migrationen in einer Transaktion. Angewendete Migrationen stehen in `schema_migrations`; parallele Starts sind per `pg_advisory_lock` abgesichert. Eine leere Datenbank ist danach sofort einsatzbereit, ohne manuelle Schritte.

Es gibt genau eine fachliche Tabelle, `jobs`, plus `schema_migrations`. Ein Auftrag ist zugleich das Ergebnis.

| Spalte | Typ | Bedeutung |
| --- | --- | --- |
| `id` | UUID, PK | Verarbeitungs-ID |
| `youtube_id` | TEXT, Index | Normalisierte Video-ID |
| `title` | TEXT | Videotitel für Anzeige und Liste |
| `status` | TEXT | `queued`, `running`, `done`, `failed` |
| `stage` | TEXT | Aktuelle Stufe während `running` |
| `progress` | REAL | 0–1 |
| `options` | JSONB | Wirksame Parameter des Auftrags |
| `error` | TEXT | Fehlermeldung bei `failed` |
| `attempts` | SMALLINT | Anzahl Starts (für Wiederaufnahme) |
| `created_at`, `started_at`, `finished_at`, `heartbeat_at` | TIMESTAMPTZ | Zeitpunkte |
| `package_file` | TEXT | Dateiname im Paket-Volume (bei `done`) |
| `package_size` | BIGINT | Größe in Bytes |
| `screenshot_count` | INTEGER | Für die Anzeige |

Index auf `(youtube_id, created_at DESC)` für „neuester Auftrag zu diesem Video“. Alles Weitere (Kanal, Dauer, Kapitel, Transkript) liest das Web-UI bei Bedarf aus `meta.json` im ZIP, ohne es zu entpacken.

Statuswerte: `queued` → `running` → `done` oder `failed`. Während `running` zeigt `stage` die Stufe (`download`, `transcribe`, `detect`, `assemble`, `store`).

ZIP-Dateien werden zuerst unter einem temporären Namen geschrieben und dann atomar umbenannt; erst danach wird der Auftrag auf `done` gesetzt. Fehlt eine Datei, auf die ein Auftrag verweist (Volume gelöscht), zeigt das Web-UI „Paket fehlt“ und bietet nur „Erneut verarbeiten“ an.

### 9.3 Warteschlange und Worker

- Worker holen Aufträge mit `SELECT … FOR UPDATE SKIP LOCKED` in Reihenfolge `created_at`.
- Laufende Aufträge aktualisieren `heartbeat_at` alle 10 s. Beim Start und regelmäßig werden Aufträge mit Status `running` und einem Heartbeat älter als 5 Minuten auf `queued` zurückgesetzt (höchstens 3 Starts laut `attempts`, danach `failed`).
- Ein neuer Auftrag für ein Video, für das schon ein Auftrag `queued` oder `running` ist, legt keinen zweiten an, sondern liefert die bestehende Verarbeitungs-ID zurück.
- „Erneut verarbeiten“ legt einen neuen Auftrag an. `STANDBILD_KEEP_RESULTS` (Voreinstellung `3`) begrenzt die Aufträge pro Video; ältere werden samt ZIP gelöscht. Fehlgeschlagene Aufträge werden nach 30 Tagen gelöscht, außer es ist der neueste eines Videos.
- Bei Fehlern speichert `error` eine kurze, verständliche Meldung (z. B. „Video ist altersbeschränkt“), das vollständige Protokoll geht ins Container-Log.

## 10. Web-UI

Eine einzelne, schlichte Seite unter `/`, serverseitig mit Jinja2 gerendert und mit wenig Vanilla-JavaScript (kein Build-Schritt, keine externen CDNs, alle Assets im Image). Sprache der Oberfläche: Deutsch.

### 10.1 Ablauf

1. Ein Eingabefeld „YouTube-Link einfügen“. Beim Einfügen oder Tippen (300 ms Verzögerung) fragt die Seite `GET /api/videos/lookup?url=…`; die Normalisierung erfolgt auf dem Server.
2. Je nach Antwort erscheint unter dem Feld eine Karte:

| Zustand | Anzeige | Aktionen |
| --- | --- | --- |
| Ungültiger Link | Fehlermeldung | – |
| Unbekanntes Video | Video-ID, falls möglich Titel und Vorschaubild (per yt-dlp-Metadaten, ohne Download) | **Verarbeiten** |
| In Bearbeitung | Titel, Stufe, Fortschrittsbalken, Laufzeit | (keine; Aktualisierung alle 2 s) |
| Vorhanden | Titel, Kanal, Dauer, Datum der Verarbeitung, Anzahl Screenshots, Paketgröße, verwendete Parameter (aufklappbar) | **Herunterladen**, **Erneut verarbeiten** |
| Fehlgeschlagen oder Paket fehlt | Titel, Fehlermeldung | **Erneut verarbeiten** |

3. Nach **Verarbeiten** oder **Erneut verarbeiten** wechselt die Karte in den Zustand „In Bearbeitung“ und anschließend automatisch zu „Vorhanden“.

**Parameter pro Auftrag:** Neben den Buttons **Verarbeiten** und **Erneut verarbeiten** steht ein aufklappbarer Bereich „Parameter“ mit einem Eingabefeld je Schlüssel aus Tabelle 4.3:

- **Vorbelegung:** Ist der eingefügte Link bereits einmal verarbeitet worden, werden alle Felder automatisch mit den zuletzt verwendeten Parametern dieses Videos gesetzt (`options` des neuesten Auftrags, unabhängig von dessen Status). Ein Hinweis „Parameter vom TT.MM.JJJJ übernommen“ steht über den Feldern. Ist ein zuletzt verwendetes Modell nicht mehr installiert, wird das Standardmodell gesetzt und darauf hingewiesen.
- Bei unbekannten Videos sind die Felder mit den wirksamen Voreinstellungen vorbelegt (Umgebungsvariablen bzw. eingebaute Werte), geliefert von `GET /api/defaults`.
- Ein Profil-Auswahlfeld setzt die Schwellenfelder auf die Profilwerte; einzelne Felder lassen sich danach weiter ändern.
- Das Modell-Feld ist eine Auswahl der installierten Modelle.
- Masken werden als Zahlenquartette eingegeben (`x, y, w, h` relativ 0–1), eine Zeile pro Maske.
- Die Prüfung der Wertebereiche erfolgt im Browser und auf dem Server; Fehler erscheinen direkt am Feld.
- „Auf Voreinstellungen zurücksetzen“ stellt alle Felder auf die Voreinstellungen zurück.

Unter der Karte steht eine Liste der letzten 20 Aufträge (Titel, Status, Datum, Download-Link), damit man nicht jeden Link erneut einfügen muss.

### 10.2 REST-API (vom Web-UI genutzt)

| Methode und Pfad | Zweck |
| --- | --- |
| `GET /api/videos/lookup?url=` | Normalisieren, Zustand wie in der Tabelle oben, neueste Verarbeitungs-ID, zuletzt verwendete Parameter |
| `GET /api/defaults` | Wirksame Voreinstellungen, Profile, Wertebereiche, installierte Modelle |
| `POST /api/jobs` `{url, force?, options?}` | Auftrag anlegen, Antwort `{job_id, status, reused}` |
| `GET /api/jobs/{id}` | Status (gleiches Format wie MCP `status`) |
| `GET /api/jobs/{id}/package` | ZIP herunterladen (`Content-Disposition` mit Paketname) |
| `GET /api/jobs?limit=20` | Letzte Aufträge |
| `GET /api/models` | Modelle mit Status, Größe, Standardmodell |
| `POST /api/models` `{url, name?}` | Modell von HuggingFace hinzufügen (10.3) |
| `DELETE /api/models/{name}` | Modell entfernen |
| `PUT /api/models/{name}/default` | Als Standardmodell setzen |
| `GET /healthz` | Lebenszeichen inkl. DB-Verbindung und beschreibbarer Volumes |

OpenAPI-Dokumentation unter `/api/docs`.

### 10.3 Whisper-Modelle verwalten

Ein zweiter Bereich der Seite (Reiter „Modelle“) zeigt alle Modelle mit Name, Quelle, Größe, Status und einer Markierung für das Standardmodell. Nach dem ersten Start ist dort `large-v3-turbo` als Standardmodell eingetragen.

**Modell hinzufügen:** Ein Eingabefeld nimmt einen HuggingFace-Link oder eine Repo-ID an, z. B. `https://huggingface.co/Systran/faster-whisper-medium` oder `Systran/faster-whisper-medium`, optional mit Revision (`…/tree/<branch>`).

1. Der Server prüft per HuggingFace-API (ohne vollständigen Download), ob das Repo ein faster-whisper-taugliches CTranslate2-Modell enthält: `model.bin`, `config.json` und `tokenizer.json` oder `vocabulary.*`. Andernfalls Fehlermeldung: „Kein CTranslate2-Modell. Bitte eine faster-whisper-Variante wählen.“ Eine Konvertierung von Transformers-Modellen gehört nicht zum Umfang (sie bräuchte PyTorch).
2. Der Name ergibt sich aus dem Repo-Namen ohne Präfix `faster-whisper-` (z. B. `medium`) und kann vor dem Start geändert werden. Bereits vorhandene Namen werden abgelehnt.
3. Der Download läuft im Hintergrund mit `huggingface_hub.snapshot_download` in `/models/<name>.partial/` und wird nach Erfolg atomar umbenannt. Fortschritt in Prozent wird angezeigt. Ein optionales `HF_TOKEN` wird für zugangsbeschränkte Repos genutzt.
4. Danach steht das Modell im Parameterbereich und für MCP `options.whisper_model` zur Auswahl.

**Weitere Aktionen:** „Als Standard setzen“ und „Entfernen“. Das Standardmodell und Modelle, die gerade ein Auftrag nutzt, können nicht entfernt werden.

**Ablage ohne Datenbank:** Jedes Modellverzeichnis enthält eine kleine `standbild-model.json` (Quelle, Revision, Datum, Größe). Das Standardmodell steht in `/models/default.txt`. Die Modellliste ergibt sich aus den Verzeichnissen mit dieser Datei; ein laufender Download ist an `<name>.partial/` erkennbar, sein Fortschritt wird im Speicher gehalten. Abgebrochene Downloads werden beim Start gelöscht.

## 11. MCP-Endpunkt

Der MCP-Server läuft unter `/mcp` mit dem Transport **Streamable HTTP** (offizielles Python-SDK, FastMCP, zustandslos). Ist `STANDBILD_API_TOKEN` gesetzt, wird `Authorization: Bearer <token>` verlangt. Servername: `standbild`. Er bietet genau drei Tools.

### 11.1 `process`

- **Parameter:** `url` (string, Pflicht), `force` (bool, Voreinstellung `false`), `options` (Objekt, optional) mit beliebigen Schlüsseln aus Tabelle 4.3, z. B. `profile`, `threshold`, `min_local_change`, `stable_time`, `min_gap`, `whisper_model`, `masks`. Das JSON-Schema des Tools listet alle Schlüssel mit Typ, Wertebereich und Voreinstellung, damit ein Agent sie ohne Raten setzen kann. Unbekannte Schlüssel oder Werte außerhalb des Bereichs → Tool-Fehler mit genauer Meldung.
- **Verhalten:** Die übergebenen `options` werden mit Profil und Voreinstellungen zu den wirksamen Parametern zusammengeführt (Rangfolge aus Abschnitt 4). Gibt es bereits ein erfolgreiches Ergebnis mit **denselben wirksamen Parametern** und `force` ist `false`, kommt dessen Verarbeitungs-ID zurück (`reused: true`). Weichen die Parameter ab, wird automatisch neu verarbeitet. Läuft schon ein Auftrag für das Video, kommt dessen ID zurück. Ungültiger Link → Tool-Fehler.
- **Beispielaufruf:**

```json
{"url": "https://youtu.be/abc123", "options": {"profile": "coding", "threshold": 0.03, "masks": [[0.78, 0.72, 0.22, 0.28]]}}
```

- **Antwort** (strukturiert, zusätzlich als Text), inklusive der wirksamen Parameter:

```json
{"job_id": "7f3c…", "status": "queued", "reused": false, "youtube_id": "abc123", "title": "Docker Compose in 20 Minuten",
 "options": {"profile": "coding", "threshold": 0.03, "min_local_change": 0.002, "whisper_model": "large-v3-turbo", "…": "…"}}
```

### 11.2 `status`

- **Parameter:** `job_id` (string, Pflicht)
- **Antwort:**

```json
{"job_id": "7f3c…", "status": "running", "stage": "transcribe", "progress": 0.42,
 "title": "…", "created_at": "…", "finished_at": null, "error": null,
 "screenshot_count": null, "package_size": null}
```

Unbekannte ID → Tool-Fehler „Verarbeitungs-ID nicht gefunden“. Die Tool-Beschreibung empfiehlt dem aufrufenden Agenten, höchstens alle 15 s abzufragen.

### 11.3 `get`

- **Parameter:** `job_id` (string, Pflicht)
- **Antwort bei `done`:** das ZIP als eingebettete Ressource (`EmbeddedResource` mit `blob` in Base64, `mimeType: application/zip`, URI `standbild://packages/<job_id>.zip`) plus ein Textteil mit Paketname, Größe, SHA-256 und Download-URL (`STANDBILD_PUBLIC_URL/api/jobs/<id>/package`).
- **Große Pakete:** Überschreitet das ZIP `STANDBILD_MCP_MAX_INLINE_MB` (Voreinstellung 25), wird es nicht eingebettet. Die Antwort enthält dann nur den Textteil mit Download-URL und den Hinweis, dass das Paket zu groß für eine Einbettung ist. Hintergrund: Base64 vergrößert die Daten um ein Drittel, und viele MCP-Clients kommen mit sehr großen Antworten schlecht zurecht.
- **Nicht fertig** (`queued`/`running`/`failed`): Tool-Fehler mit aktuellem Status bzw. Fehlermeldung.

### 11.4 Beispiel-Einbindung

```json
{"mcpServers": {"standbild": {"type": "http", "url": "http://nas.local:8080/mcp",
  "headers": {"Authorization": "Bearer <token>"}}}}
```

## 12. Docker und Deployment

Ein einziges Dockerfile erzeugt ein eigenständiges Image. Nach `docker build` und `docker push` in die lokale Registry genügen auf dem Zielsystem `docker pull` und `docker run` mit den DB-Variablen. Ein Compose-File ist nicht nötig.

### 12.1 Inhalt des Images

- Basis `python:3.12-slim-bookworm` (Multi-Stage: Build-Stufe mit Compilern, schlanke Laufzeitstufe)
- ffmpeg, yt-dlp in der beim Build aktuellen Version
- Whisper-Modell `large-v3-turbo` (ca. 1,6 GB) als Startausstattung unter `/opt/standbild/seed-models`, wird beim ersten Start in das Modell-Volume kopiert (Abschnitt 6)
- Python-Paket mit allen Abhängigkeiten, Web-Assets, Vorlagen und SQL-Migrationen
- Nicht-Root-Benutzer `standbild` (UID/GID 1000), `EXPOSE 8080`, `HEALTHCHECK` auf `/healthz`, `CMD ["standbild", "serve"]`

Build-Argumente: `SEED_MODEL` (Voreinstellung `large-v3-turbo`), `BASE_IMAGE` (für eine optionale CUDA-Variante auf `nvidia/cuda:…-cudnn-runtime`), `YTDLP_VERSION` (leer = neueste). Ziel für das CPU-Image: ≤ 3,5 GB.

### 12.2 Volumes

```dockerfile
VOLUME ["/tmp", "/data/packages", "/models"]
```

| Volume | Inhalt | Größenordnung |
| --- | --- | --- |
| `/tmp` | Videodownload und Zwischendateien je Auftrag (`/tmp/standbild/<job_id>/`), nach Abschluss gelöscht | einige GB pro laufendem Auftrag |
| `/data/packages` | Ergebnis-ZIPs (`<youtube-id>_<yyyymmdd-hhmm>.zip`) | 5–50 MB pro Paket |
| `/models` | Whisper-Modelle, ein Unterverzeichnis pro Modell | 0,5–3 GB pro Modell |

Die Verzeichnisse gehören im Image dem Benutzer `standbild`. Beim Start prüft der Dienst, ob alle drei beschreibbar sind, und bricht sonst mit einer klaren Meldung ab (häufige Ursache: Bind-Mount mit anderem Besitzer).

### 12.3 Bauen und Bereitstellen

```bash
docker build -t registry.local:5000/standbild:1.0.0 -t registry.local:5000/standbild:latest .
docker push registry.local:5000/standbild:1.0.0
docker push registry.local:5000/standbild:latest
```

Ein `Makefile` kapselt das mit `REGISTRY` und `VERSION` als Variablen. Die Version stammt aus `pyproject.toml`.

### 12.4 Starten

```bash
docker run -d --name standbild --restart unless-stopped -p 8080:8080 \
  -v standbild-tmp:/tmp -v standbild-packages:/data/packages -v standbild-models:/models \
  -e STANDBILD_DB_HOST=db.local -e STANDBILD_DB_NAME=standbild \
  -e STANDBILD_DB_USER=standbild -e STANDBILD_DB_PASSWORD=geheim \
  -e STANDBILD_PUBLIC_URL=http://nas.local:8080 \
  registry.local:5000/standbild:latest
```

Ohne `-v` legt Docker anonyme Volumes an; für Pakete und Modelle sind benannte Volumes oder Bind-Mounts sinnvoll, damit sie Updates des Containers überleben. GPU-Variante zusätzlich mit `--gpus all`.

### 12.5 yt-dlp aktuell halten

YouTube ändert regelmäßig Details, die neue yt-dlp-Versionen erfordern. Zwei Wege:

- Regulär: Image neu bauen (yt-dlp wird dabei aktualisiert) und neu ausrollen.
- Optional: `STANDBILD_YTDLP_AUTOUPDATE=true` aktualisiert yt-dlp beim Containerstart per pip in ein beschreibbares Verzeichnis. Schlägt das fehl, wird die mitgelieferte Version verwendet. Voreinstellung `false`.

Schlägt ein Download mit einem typischen yt-dlp-Fehler fehl, nennt die Fehlermeldung im Auftrag den Hinweis „yt-dlp evtl. veraltet – Image neu bauen“.

## 13. Projektstruktur, Tests, Akzeptanzkriterien

### 13.1 Struktur

```
standbild/
├── pyproject.toml
├── Dockerfile
├── Makefile              # build / push mit REGISTRY und VERSION
├── README.md
├── DECISIONS.md
├── src/standbild/
│   ├── cli.py            # typer-Befehle
│   ├── config.py         # pydantic-settings, Parameter-Schema, Profile, Rangfolge
│   ├── youtube.py        # URL-Normalisierung, Metadaten
│   ├── models.py         # Whisper-Modelle im Volume: Seed, HF prüfen/laden, Standard, entfernen
│   ├── pipeline/
│   │   ├── fetch.py      # Download nach /tmp
│   │   ├── transcribe.py
│   │   ├── detect.py     # reine Messfunktionen + Zustandsautomat
│   │   ├── align.py      # Einfügeregel
│   │   └── package.py    # transcript.md, AGENTS.md, Protokoll, ZIP
│   ├── db/
│   │   ├── pool.py
│   │   ├── repo.py       # Tabelle jobs (einzige fachliche Tabelle)
│   │   └── migrations/001_init.sql …
│   ├── worker.py         # Warteschlange, Heartbeat, Wiederaufnahme, Aufräumen
│   ├── web/
│   │   ├── app.py        # FastAPI: Seite, REST, Healthcheck, MCP-Mount
│   │   ├── templates/index.html
│   │   └── static/app.js, app.css
│   ├── mcp_server.py     # Tools process, status, get
│   ├── preview.py
│   └── templates/AGENTS.md.j2
└── tests/
```

### 13.2 Tests

- **Synthetische Videos** werden im Test mit OpenCV erzeugt, keine Downloads im Test. Szenarien: (a) 3 Folien mit harten Schnitten, (b) Folien mit 0,5 s Überblendung, (c) Editor, in dem nacheinander 5 Textzeilen erscheinen, (d) Mauszeiger, der 10 s über eine statische Folie wandert, (e) Webcam-Ecke mit Rauschen und Maske, (f) Rückkehr zu Folie 1 (Duplikat), (g) 20 s Scrollen.
- **Erwartung je Szenario:** (a) 3 Bilder, (b) 3 Bilder, keines halb überblendet, (c) mit Profil `coding` 5 neue Bilder, (d) 1 Bild, (e) 1 Bild, (f) Verweis auf `frame_0001`, (g) Grenze `max_per_minute` eingehalten.
- **Ausrichtung:** Unit-Tests für `align.py` mit künstlichen Wort-Zeitstempeln (Satzgrenze in Toleranz, außerhalb, mehrere Bilder an einer Grenze, Bild in Sprechpause).
- **Parameter:** Rangfolge Auftrag > Profil > Umgebung > eingebaut; Wertebereiche; „selbe wirksame Parameter → wiederverwenden“, „abweichende → neu verarbeiten“.
- **URL-Normalisierung:** Tabelle mit mindestens 15 gültigen und ungültigen Eingaben.
- **Datenbank:** Integrationstests gegen einen leeren Postgres in `testcontainers` (Migrationen von null, Warteschlange mit zwei parallelen Workern, Wiederaufnahme nach abgelaufenem Heartbeat, kein doppelter Auftrag, `KEEP_RESULTS` löscht auch ZIPs).
- **Modelle:** HuggingFace-Aufrufe durch Attrappe ersetzt: gültiges CTranslate2-Repo, Repo ohne `model.bin`, abgebrochener Download, Entfernen des Standardmodells verweigert, Seed-Kopie beim Start.
- **API und MCP:** FastAPI-`TestClient` für alle REST-Pfade; MCP-Tools über den In-Memory-Client des SDK (inkl. `options`, `get` mit eingebettetem ZIP und mit Größenüberschreitung). Die Pipeline wird dabei durch eine Attrappe ersetzt.
- **Transkription:** ein Integrationstest mit einer kurzen WAV-Datei im Repository und dem Modell `tiny`, als langsamer Test markiert.
- **Paket:** Prüfung, dass jeder Bildverweis auf eine Datei im ZIP zeigt und `AGENTS.md` alle Pflichtabschnitte enthält.
- `ruff` + `mypy --strict` für `src/`, Testabdeckung ≥ 80 % für `detect.py`, `align.py`, `config.py`, `youtube.py`, `models.py` und `worker.py`.

### 13.3 Akzeptanzkriterien

- [ ] `docker build` ohne weitere Schritte erfolgreich; das Image läuft nach `docker pull` nur mit den DB-Variablen und den drei Volumes.
- [ ] Gegen eine leere Datenbank legt der erste Start alle Tabellen an; ein zweiter Container am selben Schema stört nicht.
- [ ] Nach dem ersten Start ist `large-v3-turbo` im Modell-Volume und als Standardmodell eingetragen, ohne Netzwerkzugriff.
- [ ] Ein über das Web-UI hinzugefügtes HuggingFace-Modell (z. B. `Systran/faster-whisper-small`) steht danach im Parameterbereich und für MCP zur Auswahl und überlebt einen Container-Neustart.
- [ ] Zur Laufzeit Netzwerkzugriffe nur zu Postgres, beim Videodownload und beim ausdrücklichen Hinzufügen eines Modells.
- [ ] Web-UI: Link einfügen zeigt je nach Zustand „Verarbeiten“ oder „Herunterladen“ + „Erneut verarbeiten“; Parameter lassen sich vor beiden Aktionen ändern; Fortschritt aktualisiert sich ohne Neuladen.
- [ ] MCP: `process` (mit und ohne `options`) → `status` bis `done` → `get` liefert ein gültiges ZIP, getestet mit einem echten MCP-Client (z. B. MCP Inspector).
- [ ] Das ZIP enthält `AGENTS.md`, `transcript.md`, `frames/`, `meta.json` und `log/`; jeder Bildverweis zeigt auf eine existierende Datei; die Bilder werden in einem Markdown-Viewer angezeigt.
- [ ] Screenshots haben exakt die Auflösung des geladenen Videos.
- [ ] Nach einem Container-Neustart während der Verarbeitung wird der Auftrag automatisch fortgesetzt; `/tmp` enthält danach keine Reste.
- [ ] Alle Tests aus 13.2 grün.
- [ ] README mit Build, Push, Start, Volumes, allen Variablen und Parametern, MCP-Einbindung, Modellverwaltung, Einstellen der Schwellen per `preview` und Hinweis zu den YouTube-Nutzungsbedingungen.

## 14. Hinweise für die Umsetzung

Empfohlene Reihenfolge, damit nach jedem Schritt etwas Lauffähiges vorliegt:

1. Gerüst: `pyproject.toml`, Konfiguration mit Parameter-Schema und Rangfolge, CLI-Gerüst, Dockerfile mit Seed-Modell und Volumes.
2. `detect.py` mit den synthetischen Testvideos aus 13.2. Das ist der kritische Teil; erst weitermachen, wenn alle Szenarien grün sind.
3. `fetch.py`, `transcribe.py`, `align.py`, `package.py` inkl. `AGENTS.md`; danach `standbild run` Ende-zu-Ende mit einem echten Video.
4. Datenbank: Migrationen, Repository, Worker mit Warteschlange und Wiederaufnahme.
5. REST-API und Web-UI inkl. Parameterbereich.
6. Modellverwaltung (`models.py`, Reiter „Modelle“).
7. MCP-Endpunkt.
8. `preview`, Makefile für Build und Push, README.

Vorgaben an die Implementierung:

- Alle Schwellwerte und Konstanten aus dieser Spezifikation gehören in `config.py`, nicht verstreut in den Code.
- `detect.py` trennt reine Berechnung (Messwerte aus zwei Bildern) vom Zustandsautomaten, damit beides einzeln testbar ist.
- Keine Abhängigkeit von PyTorch in der Kernpipeline (hält das Image klein). `faster-whisper` braucht es nicht.
- Abweichungen von dieser Spezifikation sind erlaubt, wenn sie begründet und in `DECISIONS.md` festgehalten werden.

Offene Punkte, die nach den ersten echten Tests zu entscheiden sind:

- Ob die Voreinstellungen in 7.8 für typische Videos passen.
- Ob Cursorbewegungen trotz Mindestfläche stören und eine eigene Zeigererkennung nötig ist.

Quellen: [slidegeist](https://pypi.org/project/slidegeist/), [video-transcriber](https://pypi.org/project/video-transcriber/), [slidecap](https://pypi.org/project/slidecap/), [slidoc](https://github.com/shyenx/slidoc), [vidwise](https://pypi.org/project/vidwise/).
