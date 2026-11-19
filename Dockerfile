# PaperJay: docker run -p 8000:8000 -p 8443:8443 -v paperjay-data:/data ghcr.io/3g15h1/paperjay
FROM python:3.13-slim-trixie

# OCR tools, and Tesseract languages for the most common European languages (more: build with
# --build-arg OCR_LANGS="eng deu …", Debian package names tesseract-ocr-<lang>).
ARG OCR_LANGS="eng deu fra spa ita nld por"
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr ghostscript unpaper poppler-utils pngquant tzdata fonts-dejavu-core \
    $(for l in $OCR_LANGS; do echo tesseract-ocr-$l; done) \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data HOST=0.0.0.0 PORT=8000 HTTPS_PORT=8443 PAPERJAY_INSTALL=docker
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY paperjay ./paperjay
COPY deploy/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN pip install . && mkdir -p /data
# the built-in search model (about 130 MB) inside the image, so search by meaning works offline from the start
ENV MODELS_DIR=/opt/paperjay-models
RUN paperjay fetch-models && chmod -R a+rX /opt/paperjay-models

# Runs as PUID/PGID (default 1000), see docker-entrypoint.sh
VOLUME /data
EXPOSE 8000 8443
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/healthz', timeout=4)" || exit 1
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["paperjay", "serve"]
