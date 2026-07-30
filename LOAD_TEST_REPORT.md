# Load test report

## SLO proposal

| Signal | Staging acceptance target |
| --- | --- |
| Read-only API p95 | <= 400 ms |
| Write API p95 | <= 800 ms |
| HTTP 5xx rate | < 0.5% excluding deliberate negative tests |
| WebSocket connect/reconnect p95 | <= 1 s / <= 3 s |
| Celery task latency p95 | <= 60 s for normal-priority synthetic tasks |
| Queue lag | <= 30 s during steady state |

## Required scenarios

Use synthetic accounts and disposable objects only. The planned k6 suite must cover login, home/dashboard data, file access-ticket retrieval, print quote/order with idempotency key, feedback submit and analytics, support ticket/message, chat REST, and chat WebSocket connect/reconnect. Capture RPS, p50/p95/p99, error classes, database CPU/connections, Redis memory/queue lag, worker throughput, and WebSocket connections.

## Evidence status

No Staging environment, synthetic test account, or load-run artifact was available in this workspace. No SLO is claimed as passed. Before promotion, attach the exact k6 command, Git SHA, dataset description, runtime configuration, charts, raw result JSON, remediation decisions, and rerun evidence here.
