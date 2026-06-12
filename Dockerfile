FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV DJANGO_SETTINGS_MODULE=config.settings.production
ENV PORT=8000
ENV HEALTHCHECK_PATH=/api/v1/health/

WORKDIR /app

RUN addgroup --system panorama
RUN adduser --system --ingroup panorama --home /app panorama
RUN mkdir -p /app/staticfiles /app/media
RUN chown -R panorama:panorama /app

COPY requirements/ ./requirements/

RUN pip install --upgrade pip
RUN pip install -r requirements/production.txt

COPY --chown=panorama:panorama . .

RUN chmod +x /app/docker/entrypoint.sh
RUN chown -R panorama:panorama /app/staticfiles /app/media

WORKDIR /app/app

USER panorama

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 CMD python /app/docker/healthcheck.py

ENTRYPOINT ["/app/docker/entrypoint.sh"]

CMD ["sh", "-c", "daphne -b 0.0.0.0 -p ${PORT:-8000} config.asgi:application"]