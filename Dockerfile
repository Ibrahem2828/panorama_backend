FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings.production \
    PORT=8000

WORKDIR /app

RUN addgroup --system panorama \
    && adduser --system --ingroup panorama --home /app panorama \
    && mkdir -p /app/staticfiles /app/media \
    && chown -R panorama:panorama /app

COPY requirements/ ./requirements/
RUN pip install --upgrade pip \
    && pip install -r requirements/production.txt

COPY --chown=panorama:panorama . .
RUN chmod +x /app/docker/entrypoint.sh \
    && chown -R panorama:panorama /app/staticfiles /app/media

WORKDIR /app/app

USER panorama

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 CMD python -c "import http.client; c=http.client.HTTPConnection('127.0.0.1', 8000, timeout=5); c.request('GET','/api/health/'); r=c.getresponse(); exit(0 if 200 <= r.status < 400 else 1)"
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["sh", "-c", "daphne -b 0.0.0.0 -p ${PORT:-8000} config.asgi:application"]
