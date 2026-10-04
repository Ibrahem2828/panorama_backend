# Backend test report — 2026-08-13

| Gate | Result |
| --- | --- |
| Django check (testing) | PASS — 0 issues |
| `makemigrations --check` | PASS — no changes |
| `pytest -q` | PASS — 127 tests |
| Chat-ticket WebSocket subset | PASS — 7 tests |
| Ruff check / formatting | PASS — 232 files formatted |
| Mypy | PASS — 0 errors in 246 source files |
| OpenAPI validate / drift / Postman validation | PASS — 272/272 operations |
| Bandit medium/high | PASS — no findings reported |
| `pip check` | PASS — no broken requirements |
| `pip-audit requirements.lock` | NOT VERIFIED — advisory-service request timed out locally |
| Docker build/runtime | NOT VERIFIED — Docker Desktop Linux daemon unavailable |
