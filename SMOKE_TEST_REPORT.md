# Smoke test report

Status: **BLOCKED** on 2026-07-30.

`scripts/smoke_test.py` exists but requires an HTTPS Staging URL and a synthetic bearer token. No such target or credential was supplied, so it was not run. The required flows—health, registration/OTP/login/refresh/logout, verification review, groups/messages/WebSocket, protected file tickets, print idempotency, notifications, support, feedback, RBAC negatives, and error contracts—have no deployed smoke evidence.

Local unit/integration tests are not substituted for Staging smoke tests in this report.
