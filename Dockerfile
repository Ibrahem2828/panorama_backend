# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/ ./requirements/
RUN python -m pip wheel --wheel-dir /wheels -r requirements/production.txt


FROM python:3.12-slim AS runtime

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
    && mkdir -p /app/app/staticfiles /app/app/media \
    && chown -R panorama:panorama /app

USER panorama
WORKDIR /app/app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/v1/health/' % os.getenv('PORT','8000'), timeout=3).read()" || exit 1

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["sh", "-c", "daphne -b 0.0.0.0 -p ${PORT:-8000} config.asgi:application"]
