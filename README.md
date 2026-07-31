# Panorama Backend

Panorama is a Django/DRF backend for the student mobile application and its
administrative dashboard. It uses PostgreSQL, Redis, Celery, Daphne/Channels,
and private local persistent storage.

The repository is a production candidate, not a production approval. Docker,
Coolify, PostgreSQL/Redis/Celery runtime, persistent-volume restart/redeploy,
backup restore, security testing, and load testing must be evidenced in a
matching staging environment before release.

## Documentation and API contract

Start at [docs/INDEX.md](docs/INDEX.md). The generated API contract is available
as [OpenAPI JSON](docs/api/openapi.json), [OpenAPI YAML](docs/api/openapi.yaml),
and a Postman collection in the same directory.

## Local verification

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe app\manage.py check --settings=config.settings.testing
.venv\Scripts\python.exe app\manage.py makemigrations --check --dry-run --settings=config.settings.testing
.venv\Scripts\python.exe app\manage.py storage_status --settings=config.settings.testing --write-test
.venv\Scripts\python.exe app\manage.py document_pipeline_status --settings=config.settings.testing
```

Testing uses SQLite, in-memory Channels, and local cache. It is intentionally
not proof of PostgreSQL query plans, Redis behaviour, Docker image content, or
Coolify runtime. Use `.env.example` only as a variable inventory; do not commit
runtime secrets.
