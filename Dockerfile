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

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 CMD python -c "import http.client, os, sys; port=int(os.environ.get('PORT', '8000')); host=os.environ.get('HEALTHCHECK_HOST') or (os.environ.get('ALLOWED_HOSTS') or os.environ.get('DJANGO_ALLOWED_HOSTS') or 'localhost').split(',')[0].strip(); conn=http.client.HTTPConnection('127.0.0.1', port, timeout=3); conn.request('GET', '/api/v1/health/ready/', headers={'Host': host}); response=conn.getresponse(); sys.exit(0 if response.status < 500 else 1)"

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["sh", "-c", "daphne -b 0.0.0.0 -p ${PORT:-8000} config.asgi:application"]
