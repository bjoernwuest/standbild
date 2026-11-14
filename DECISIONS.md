# Entscheidungen und Abweichungen von der Spezifikation

Jede Abweichung ist begründet. Nummern in Klammern verweisen auf Abschnitte der Spezifikation.

## Transkription (6)

1. **`condition_on_previous_text=True` statt `False`, dazu der Videotitel als Hinweis.** Mit der Einstellung
   aus der Spezifikation liefert `large-v3-turbo` für lange Strecken Text ohne Satzzeichen und Großschreibung
   und schreibt Produktnamen falsch. Gemessen an den drei Testvideos (Satzzeichen `.?!` im Transkript):

   | Video | Spezifikation | nur Titel-Hinweis | nur Kontext | Kontext + Titel (gewählt) |
   | --- | --- | --- | --- | --- |
   | JBPsddHozgQ (4:32) | 25 | 39 | 54 | 55 |
   | C9n5aST3WcU (4:24) | 23, „LINA-X“/„Linux“ | 29, „LeanIX“, ein Satz fehlt | 35, „LINA-X“ | 41, „LeanIX“ |
   | dCG6P_Sx7qM (15:43) | 58 bei 2.380 Wörtern | – | 128 | 138 |

   Ohne Satzzeichen findet die Einfügeregel (8.1) keine Satzgrenzen und zerreißt Sätze. Der Titel wird als
   `hotwords` in jedes 30-Sekunden-Fenster gegeben (nur bei echten Metadaten, nicht bei Dateinamen).
   Zurück zum Verhalten der Spezifikation: `STANDBILD_CONDITION_ON_PREVIOUS_TEXT=false`.
   Auch die Videobeschreibung als längerer Hinweis wurde getestet und verworfen: Sie korrigiert einzelne
   Begriffe („Claude“ statt „Cloud“), führt aber zu gestotterten Wiederholungen und zu Text aus der
   Beschreibung im Transkript.
2. **Halluzinationen werden zusätzlich gefiltert.** Geblieben sind VAD-Filter und das Entfernen von mehr als
   drei wörtlichen Wiederholungen. Neu: Segmente, die erst in den letzten 0,2 s der Tonspur „beginnen“ oder
   mehr als 40 Zeichen pro Sekunde „sprechen“, werden verworfen. So sah der Text aus, den Whisper nach dem
   letzten gesprochenen Satz erfand („Thank you.“, „Now I will see you through the project.“,
   „www.fema.gov.au“): bis zu 500 Zeichen/s, während echte Segmente der Testvideos bei höchstens 32 lagen.
   Alles Entfernte wird im Protokoll gezählt.
3. **Der Ton wird selbst eingelesen** (`audio.wav`, 16 kHz mono) und als Array übergeben. Der Dekoder von
   faster-whisper 1.2.1 ruft PyAV mit einem Argument auf, das aktuelle PyAV-Versionen nicht mehr kennen.
4. **Untertitel werden nur geladen, wenn `use_subtitles` gesetzt ist.** Die Spezifikation lädt `subs.vtt`
   immer; ohne `use_subtitles` würde die Datei aber weder benutzt noch ins Paket gelegt.

## Bildwechsel (7)

5. **Duplikat nur, wenn auch die Auslöseregel nicht greift (7.6).** pHash ≤ 4 und SSIM ≥ 0,98 allein erklären
   eine neue Codezeile zum Duplikat des vorherigen Bildes (beide Maße sind global). Ein früheres Bild gilt
   deshalb nur dann als dieselbe Ansicht, wenn zusätzlich weder `threshold` noch `min_local_change`
   gegenüber diesem Bild überschritten sind. Szenario (c) der Tests wäre sonst nicht erfüllbar.
6. **`min_gap` und `max_per_minute` verschieben, statt zu verwerfen (7.5).** Solange eine der Grenzen sperrt,
   bleibt der Automat im Zustand WECHSELT und speichert danach den dann aktuellen Stand – das ist „der
   jeweils letzte Kandidat“, ohne Bilder im Speicher zu puffern. `t_detect` bleibt der Beginn des Wechsels.
7. **Kein Screenshot ohne Abweichung.** Entspricht das Bild im Zustand WECHSELT wieder der Referenz, wird
   weder erzwungen gespeichert noch `max_wait` weitergezählt. Schwarz- und einfarbige Frames werden auch
   nicht erzwungen gespeichert.
8. **`motion_quiet`, Duplikat-Schwellen, Satz- und Absatzpausen** stehen als Konstanten in `config.py` und
   sind nicht pro Auftrag einstellbar (die Tabelle 4.3 führt sie nicht).
9. **Abtastung per `ffmpeg`-Pipe** (`fps=1/sample_interval`, eine der beiden Varianten aus 7.1). Videos mit
   Rotations-Metadaten werden nicht unterstützt (im Code als `shortcut:` markiert).

## Zusammenführen (8)

10. **Komma vor Wortgrenze (8.1, Regel 3).** Liegt keine Satzgrenze in 4 s Reichweite, wird zuerst eine
    Teilsatzgrenze (Komma, Semikolon, Gedankenstrich) gesucht und erst dann die nächste Wortgrenze genommen.
11. **Zeitstempel als Link:** `[00:12:34](https://www.youtube.com/watch?v=…&t=754s)`. Der Rohtext beginnt
    damit weiterhin mit `[hh:mm:ss]`.
12. **`timestamps` ist ein zusätzlicher Parameter** (Tabelle 4.3 kennt nur `--no-timestamps` der CLI). So
    gilt die Einstellung in Web-UI, MCP und CLI gleich und steht in `meta.json`.
13. **Paketname bei gleicher Minute:** Existiert `<id>_<yyyymmdd-hhmm>.zip` schon, bekommt das neue Paket
    zusätzlich Sekunden im Namen, damit „Erneut verarbeiten“ kein Ergebnis überschreibt.
14. **Quellen ohne YouTube-ID** (nur CLI `run`: lokale Datei, andere yt-dlp-Seiten) verwenden den
    bereinigten Dateinamen bzw. die ID von yt-dlp im Paketnamen.

15. **Zwischendateien `transcript.json` und `frames.json`** entstehen nur im Speicher; ins Paket kommen
    `log/frames.json` und `log/detection_log.csv`. Das Arbeitsverzeichnis wird ohnehin gelöscht.

## Parameter (4)

16. **Rangfolge mit Profil:** „Profil des Auftrags“ heißt: Der Auftrag nennt `profile` ausdrücklich. Dann
    überschreibt das Profil die Umgebungsvariablen. Ein Profil aus `STANDBILD_PROFILE` steht auf der Ebene
    der Umgebung; einzelne `STANDBILD_*`-Werte gehen ihm vor. Sonst könnten `STANDBILD_THRESHOLD` & Co. nie
    wirken, weil immer ein Profil gilt.
17. **Pfade der Volumes** sind fest wie spezifiziert, lassen sich für Tests und Läufe außerhalb des
    Containers aber über `STANDBILD_TMP_DIR`, `STANDBILD_PACKAGES_DIR`, `STANDBILD_MODELS_DIR` umbiegen.

## Datenbank und Worker (9)

18. **Kein doppelter Auftrag per Unique-Index** (`youtube_id` für `queued`/`running`) statt per
    Anwendungslogik; zwei Container können so keinen zweiten Auftrag anlegen.
19. **Heartbeat alle 2 s statt 10 s:** Fortschritt und Lebenszeichen sind ein gemeinsames `UPDATE`.
20. **Geordneter Stopp gibt laufende Aufträge sofort zurück** in die Warteschlange. Nach `docker stop` und
    Neustart läuft der Auftrag sofort weiter; nur nach einem harten Abbruch greift die 5-Minuten-Regel.
    „Fortsetzen“ heißt: Der Auftrag startet von vorn, sein Arbeitsverzeichnis wird vorher geleert.
21. **Download-Wiederholung:** Bei HTTP 403/429/5xx von YouTube versucht `fetch` es bis zu dreimal.

## Web-UI, REST, MCP (10, 11)

22. **MCP-SDK 2.x:** `FastMCP` heißt dort `MCPServer`; gleiche Bibliothek, neuer Name. Der Endpunkt
    antwortet zustandslos mit JSON (kein SSE-Strom), was Streamable HTTP ausdrücklich erlaubt.
23. **Fehlermeldungen zu `options` im MCP-Tool** kommen aus der Schemaprüfung des SDK und sind englisch,
    nennen aber Schlüssel und Grenze. Web-UI und REST melden deutsch.
24. **`/api/docs` lädt Swagger-UI im Browser von einem CDN** (Standard von FastAPI). Die eigentliche
    Oberfläche unter `/` braucht keine externen Assets; `/api/openapi.json` ist lokal.
25. **Vorschaubild** kommt direkt von `i.ytimg.com` im Browser; der Server ruft für unbekannte Videos nur
    den Titel per yt-dlp ab.
26. **`/?url=…`** öffnet die Karte zu einem Link direkt (nicht spezifiziert, zwei Zeilen JavaScript).

## Docker (12)

27. **CUDA über `--build-arg CUDA=1` statt `BASE_IMAGE`.** Das venv aus `python:3.12-slim-bookworm` läuft
    nicht auf einem `nvidia/cuda`-Basisimage (anderes Python). Stattdessen installiert `CUDA=1` die
    CUDA-Bibliotheken als Wheels (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`), wie es faster-whisper
    dokumentiert. Nicht auf echter Hardware getestet (die Entwicklungsmaschine hat keine GPU).
28. **`deno` im Image:** yt-dlp braucht für YouTube seit Ende 2025 eine JavaScript-Laufzeit.
29. **Keine Compiler in der Build-Stufe:** Alle Abhängigkeiten liegen als Wheels vor.
30. **Eigenes Stage für das Startmodell,** damit Codeänderungen die 1,6 GB nicht neu laden.

## Tests (13)

31. **`STANDBILD_TEST_DB`** (libpq-URL) lässt die Datenbanktests gegen einen vorhandenen Postgres laufen,
    wo testcontainers keine Bridge-Netzwerke anlegen kann. Jeder Test benutzt ein eigenes, neues Schema.
