# syntax=docker/dockerfile:1

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# ------------------------------------------------------------
# Builder: install production dependencies once into a venv.
# ------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_PROGRESS_BAR=off

WORKDIR /build

RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv

ENV PATH="/opt/venv/bin:${PATH}"

COPY requirements.lock /build/requirements.lock

RUN pip install \
        --no-cache-dir \
        --no-compile \
        --require-hashes \
        -r /build/requirements.lock

# ------------------------------------------------------------
# Runtime: minimal, non-root production image.
# ------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

ARG VCS_REF=unknown
ARG VERSION=unknown
ARG BUILD_DATE=unknown

LABEL org.opencontainers.image.title="panorama-backend" \
      org.opencontainers.image.description="Panorama Django ASGI backend" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="https://github.com/Ibrahem2828/panorama_backend"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/opt/venv/bin:${PATH}" \
    HOME="/home/panorama" \
    TMPDIR="/tmp/panorama" \
    PORT=8000

RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 panorama \
    && useradd \
        --uid 10001 \
        --gid 10001 \
        --create-home \
        --home-dir /home/panorama \
        --shell /usr/sbin/nologin \
        panorama

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=panorama:panorama app /app/app
COPY --chown=panorama:panorama docker /app/docker

RUN sed -i 's/\r$//' /app/docker/entrypoint.sh /app/docker/release.sh \
    && chmod 0755 /app/docker/entrypoint.sh /app/docker/release.sh \
    && mkdir -p \
        /app/app/staticfiles \
        /app/app/media \
        /app/staticfiles \
        /tmp/panorama \
        /home/panorama/.cache \
    && chown -R panorama:panorama \
        /app \
        /tmp/panorama \
        /home/panorama

USER panorama
WORKDIR /app/app

EXPOSE 8000
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=5 \
  CMD python -c "import json,os,urllib.request; port=os.getenv('PORT','8000'); req=urllib.request.Request('http://127.0.0.1:'+port+'/api/v1/health/live/',headers={'Host':os.getenv('HEALTHCHECK_HOST','localhost')}); res=urllib.request.urlopen(req,timeout=4); body=json.loads(res.read()); raise SystemExit(0 if res.status == 200 and body.get('code') == 'LIVE' and body.get('data',{}).get('status') == 'live' else 1)"

ENTRYPOINT ["/app/docker/entrypoint.sh"]

CMD ["sh", "-c", "exec daphne -b 0.0.0.0 -p ${PORT:-8000} config.asgi:application"]
