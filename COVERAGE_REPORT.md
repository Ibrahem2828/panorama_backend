# Coverage report

Date: 2026-07-30  
Command: `coverage run -m pytest -q && coverage report --fail-under=85 && coverage xml -o coverage.xml && coverage html -d htmlcov`

## Result

**PASS — 85.54% total line coverage (6,017/7,034) with 76 passing tests.**

`coverage.xml` and `htmlcov/index.html` were generated from the same run. No production module was omitted and no `pragma: no cover` remains in `app/`. Migration and test modules are present in the default full-tree calculation; the table below makes application gaps visible instead of hiding them.

## Coverage by application

| Application | Covered / statements | Coverage |
| --- | ---: | ---: |
| accounts | 974 / 1,132 | 86.04% |
| announcements | 109 / 128 | 85.16% |
| audit | 135 / 140 | 96.43% |
| chat | 270 / 335 | 80.60% |
| common | 1,236 / 1,305 | 94.71% |
| feedback | 512 / 689 | 74.31% |
| files | 251 / 349 | 71.92% |
| groups | 428 / 536 | 79.85% |
| notifications | 215 / 239 | 89.96% |
| printing | 570 / 709 | 80.39% |
| support | 319 / 383 | 83.29% |
| universities | 263 / 288 | 91.32% |
| verification | 295 / 347 | 85.01% |
| config | 232 / 246 | 94.31% |

## Lowest ten production files

| File | Coverage | Priority |
| --- | ---: | --- |
| `apps/announcements/serializers.py` | 50.00% | P2 |
| `apps/files/serializers.py` | 52.00% | P1 |
| `apps/accounts/dashboard_serializers.py` | 55.77% | P1 |
| `apps/files/services.py` | 56.00% | P1 |
| `apps/feedback/serializers.py` | 56.48% | P1 |
| `apps/accounts/dashboard_views.py` | 56.72% | P1 |
| `apps/files/views.py` | 57.29% | P1 |
| `apps/accounts/managers.py` | 57.69% | P1 |
| `apps/groups/services.py` | 58.40% | P1 |
| `apps/chat/views.py` | 58.93% | P1 |

## New behavioral coverage in this run

- Fernet encryption/decryption, malformed ciphertext, absent production key, and debug fallback.
- PDF signature/extension/empty-upload validation; page counting, corrupt-PDF rejection, content hashing, and stream-position preservation.
- Notification creation/bulk creation, active device-token update, HTTPS allowlist rejection, successful Expo delivery adapter behavior, and provider failure handling.

## Important remaining uncovered branches

Branch coverage is not enabled in the current project configuration; this report does not label line coverage as branch coverage. Priority branch scenarios still needing coverage are: PostgreSQL race conditions for printing/verification, real ASGI WebSocket connect/reconnect and expired-token paths, file quarantine/malware lifecycle, feedback policy cooldown/sampling edge cases, dashboard object-level denials, and support/printing terminal-state transition denials. These are release-risk work items even though the line-coverage gate passes.
