# syntax=docker/dockerfile:1.7
# Base-image tags are tracked by .github/dependabot.yml. CI records the resolved digest.
FROM python:3.14-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Keep dependency layers cacheable and install exclusively from the hashed lock file.
COPY requirements.lock ./
RUN python -m pip wheel --require-hashes --wheel-dir /wheels -r requirements.lock


FROM python:3.14-slim AS runtime

ARG VCS_REF=unknown
ARG VERSION=unknown
ARG BUILD_DATE=unknown

LABEL org.opencontainers.image.title="panorama-backend" \
      org.opencontainers.image.description="Panorama Django ASGI backend" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="https://github.com/REPLACE_WITH_ORGANIZATION/panorama-backend"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH=/home/panorama/.local/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system panorama \
    && useradd --system --gid panorama --create-home --home-dir /home/panorama panorama

WORKDIR /app
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* \
    && rm -rf /wheels

COPY --chown=panorama:panorama . .
RUN chmod 0755 /app/docker/entrypoint.sh /app/docker/release.sh \
    && mkdir -p /app/staticfiles \
    && chown -R panorama:panorama /app

USER panorama
WORKDIR /app/app
EXPOSE 8000

# Liveness only: readiness is configured in the Coolify service UI.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import json,os,urllib.request; port=os.getenv('PORT','8000'); request=urllib.request.Request('http://127.0.0.1:'+port+'/api/v1/health/live/',headers={'Host':os.getenv('HEALTHCHECK_HOST','localhost')}); response=urllib.request.urlopen(request,timeout=3); body=json.load(response); assert response.status == 200 and body.get('code') == 'LIVE' and body.get('data',{}).get('status') == 'live'" || exit 1

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["sh", "-c", "daphne -b 0.0.0.0 -p ${PORT:-8000} config.asgi:application"]
