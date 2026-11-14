# syntax=docker/dockerfile:1
# standbild – ein eigenständiges Image: Web-UI, REST, MCP, Worker, ffmpeg, yt-dlp und das Whisper-Startmodell.
#
#   docker build -t standbild .                      CPU-Image
#   docker build --build-arg CUDA=1 -t standbild .   zusätzlich mit CUDA-Bibliotheken (Start mit --gpus all)

# --- Startausstattung: eigenes Stage, damit Codeänderungen das Modell nicht neu laden ----------
FROM python:3.12-slim-bookworm AS seed
ARG SEED_MODEL=large-v3-turbo
RUN pip install --no-cache-dir faster-whisper \
 && python -c "from faster_whisper.utils import download_model; download_model('${SEED_MODEL}', output_dir='/opt/standbild/seed-models/${SEED_MODEL}')" \
 && rm -rf /opt/standbild/seed-models/${SEED_MODEL}/.cache

# --- Build: Paket und Abhängigkeiten in ein venv (alle Abhängigkeiten kommen als Wheels) -------
FROM python:3.12-slim-bookworm AS build
ARG YTDLP_VERSION=
ARG CUDA=0
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /src
COPY pyproject.toml ./
# Erst nur die Abhängigkeiten (mit leerem Paket), damit dieser Layer bei Codeänderungen im Cache bleibt.
# yt-dlp in der beim Build aktuellen Version, außer YTDLP_VERSION legt eine fest.
RUN mkdir -p src/standbild && touch README.md src/standbild/__init__.py \
 && pip install . "yt-dlp[default]${YTDLP_VERSION:+==${YTDLP_VERSION}}" \
 && if [ "$CUDA" = "1" ]; then pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"; fi
COPY src ./src
RUN pip install --no-deps --force-reinstall .

# --- Laufzeit ----------------------------------------------------------------------------------
FROM python:3.12-slim-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*
# JavaScript-Laufzeit, die yt-dlp für YouTube braucht
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno
COPY --from=build /opt/venv /opt/venv
COPY --from=seed /opt/standbild/seed-models /opt/standbild/seed-models
RUN groupadd --gid 1000 standbild \
 && useradd --uid 1000 --gid 1000 --create-home standbild \
 && mkdir -p /data/packages /models /tmp/standbild \
 && chown standbild:standbild /data/packages /models /tmp/standbild
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    LD_LIBRARY_PATH=/opt/venv/lib/python3.12/site-packages/nvidia/cublas/lib:/opt/venv/lib/python3.12/site-packages/nvidia/cudnn/lib
USER standbild
EXPOSE 8080
VOLUME ["/tmp", "/data/packages", "/models"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('STANDBILD_PORT', '8080'), timeout=4)"
CMD ["standbild", "serve"]
