#!/usr/bin/env bash
set -euo pipefail

DEPLOY_CHECK=0
for arg in "$@"; do
    case "$arg" in
        --deploy-check|-DeployCheck)
            DEPLOY_CHECK=1
            ;;
        *)
            echo "Unknown argument: $arg" >&2
            exit 2
            ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    PYTHON="${REPO_ROOT}/.venv/bin/python"
elif [[ -x "${REPO_ROOT}/.venv/Scripts/python.exe" ]]; then
    PYTHON="${REPO_ROOT}/.venv/Scripts/python.exe"
else
    PYTHON="python"
fi

step() {
    echo
    echo "== $1 =="
}

export PYTHONPATH="${REPO_ROOT}/app${PYTHONPATH:+:${PYTHONPATH}}"
cd "${REPO_ROOT}"

step "Python syntax check"
"${PYTHON}" - <<'PY'
from pathlib import Path

for path in sorted(Path("app").rglob("*.py")):
    if "__pycache__" not in path.parts:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
print("Python syntax check passed")
PY

step "Django system check"
"${PYTHON}" app/manage.py check

step "Django migration check"
"${PYTHON}" app/manage.py makemigrations --check --dry-run --settings config.settings.testing

if [[ "${DEPLOY_CHECK}" == "1" ]]; then
    export SECRET_KEY="${SECRET_KEY:-validation-only-secret-key-not-for-runtime-please-replace-1234567890}"
    export DEBUG="${DEBUG:-False}"
    export ALLOWED_HOSTS="${ALLOWED_HOSTS:-api.example.com}"
    export CSRF_TRUSTED_ORIGINS="${CSRF_TRUSTED_ORIGINS:-https://api.example.com,https://dashboard.example.com}"
    export CORS_ALLOWED_ORIGINS="${CORS_ALLOWED_ORIGINS:-https://dashboard.example.com}"
    export DATABASE_URL="${DATABASE_URL:-postgres://user:pass@localhost:5432/panorama}"
    export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
    export SECURE_SSL_REDIRECT="${SECURE_SSL_REDIRECT:-True}"
    export SESSION_COOKIE_SECURE="${SESSION_COOKIE_SECURE:-True}"
    export CSRF_COOKIE_SECURE="${CSRF_COOKIE_SECURE:-True}"
    export SECURE_HSTS_SECONDS="${SECURE_HSTS_SECONDS:-31536000}"
    export SECURE_HSTS_INCLUDE_SUBDOMAINS="${SECURE_HSTS_INCLUDE_SUBDOMAINS:-True}"
    export SECURE_HSTS_PRELOAD="${SECURE_HSTS_PRELOAD:-True}"

    step "Django deploy check"
    "${PYTHON}" app/manage.py check --deploy --settings config.settings.production
fi

step "API collection JSON validation"
"${PYTHON}" -c "import json, pathlib; [json.loads(pathlib.Path(p).read_text(encoding='utf-8')) for p in ('docs/api/mobile_api_collection.json', 'docs/api/dashboard_api_collection.json')]; print('API collections are valid JSON')"

step "OpenAPI schema validation"
SCHEMA_PATH="$(mktemp "${TMPDIR:-/tmp}/panorama_openapi.XXXXXX.yml")"
trap 'rm -f "${SCHEMA_PATH}"' EXIT
"${PYTHON}" app/manage.py spectacular --file "${SCHEMA_PATH}" --validate --settings config.settings.testing

step "focused pytest: API contract"
"${PYTHON}" -m pytest app/apps/common/tests_api_contract_collections.py

step "focused pytest: production hardening"
"${PYTHON}" -m pytest app/apps/common/tests_production_hardening.py

step "focused pytest: Phase 2 security"
"${PYTHON}" -m pytest app/apps/common/tests_phase2_security.py

step "focused pytest: Phase 3 reliability"
"${PYTHON}" -m pytest app/apps/common/tests_phase3_reliability.py

if [[ -f app/apps/common/tests_phase4_observability.py ]]; then
    step "focused pytest: Phase 4 observability"
    "${PYTHON}" -m pytest app/apps/common/tests_phase4_observability.py
fi

step "pytest"
"${PYTHON}" -m pytest
