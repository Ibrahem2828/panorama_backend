# Phase 3: Reliability, Performance and Data Integrity

## Purpose

Phase 3 improves production reliability, query behavior, transaction safety, cache readiness, data integrity, health checks, and cleanup foundations while preserving the current mobile and dashboard API contract.

## What Changed

- Added production Redis cache configuration using `REDIS_URL`.
- Kept local/testing cache behavior deterministic with in-memory cache.
- Added an additive readiness endpoint at `/api/v1/health/ready/`.
- Added row-level locking for critical state transitions.
- Optimized representative list querysets with `select_related` and `prefetch_related`.
- Optimized dashboard stats with aggregate queries.
- Hardened duplicate device-token upsert behavior.
- Added an idempotent OTP cleanup management command.
- Added a migration for expanded audit action choices from Phase 2/3.
- Added Phase 3 reliability regression tests.
- Added migration dry-run validation to `scripts/validate_backend.ps1`.

## Intentionally Not Changed

- No documented endpoint path was changed.
- No documented HTTP method was changed.
- No request field was renamed or removed.
- No existing response field was renamed or removed.
- The unified API response envelope was not changed.
- API collection JSON files were not modified.
- No new dependencies were added.
- No broad model redesign or heavy background-worker implementation was introduced.

## API Compatibility Guarantee

All changes are backward-compatible. Existing clients can continue using the same API paths, methods, request payloads, response fields, and pagination envelope.

The readiness endpoint is additive and does not require mobile or dashboard changes.

## Database Performance Improvements

Queryset improvements were applied where serializers already access related data:

- Print order lists now select `user` and `assigned_to`, and prefetch items and status-history users.
- Support ticket lists now select `user` and `assigned_to`, and prefetch message senders.
- Notification lists select the owning user.
- Dashboard stats now use aggregate queries for grouped counts.

No brittle query-count tests were added; the test suite focuses on behavior and contract stability.

## Indexes and Constraints

No new database indexes were added in Phase 3. Existing indexes already cover the reviewed high-traffic filters for:

- Verification status and academic filters.
- Group academic targeting and membership status.
- File visibility and academic/group targeting.
- Print status, priority, user, assignment, and history.
- Support status, priority, assignment, and messages.
- Notification read state and device token uniqueness.
- Audit action/actor/target lookups.

Existing constraints already prevent duplicate group memberships and duplicate device tokens.

## Migrations

Added:

- `app/apps/audit/migrations/0002_alter_auditlog_action.py`

This migration records the expanded audit action choices added during Phase 2 and Phase 3. It does not alter API behavior.

## Transaction Safety Improvements

Critical state-changing workflows now use transactions and row locks where practical:

- Verification review.
- Group join, leave, membership review, and role update.
- Print status changes, assignment, and internal notes.
- Support message creation, status changes, assignment, and priority changes.
- Chat message creation, deletion, and reporting.
- Device-token update/create race fallback.

Audit logging remains best-effort through the existing audit service.

## Race-Condition Protections

Representative race risks were addressed with service-level guards:

- Re-reviewing a verification locks and checks the latest status.
- Repeating a print status transition locks and checks the latest status.
- Duplicate group joins lock the membership row and preserve one membership.
- Duplicate device-token submissions update the existing token row.
- Support messages lock the ticket before checking closed/resolved status.
- Chat delete/report locks the message row.

## Status Transition Consistency

Printing continues to use the existing centralized transition map. Invalid repeated transitions are rejected and valid transitions create one history entry.

Verification review remains single-transition for pending requests.

Support tickets continue rejecting user/staff messages when resolved or closed.

## Redis and Cache Readiness

Production settings now require `REDIS_URL` and configure:

- Default Django cache with `django.core.cache.backends.redis.RedisCache`.
- DRF throttling through the shared default cache.
- Celery broker/result backend from `REDIS_URL`.
- Channels Redis layer from `REDIS_URL`.

Local/testing settings remain in-memory and do not require Redis.

## Channels and Redis

Production Channels configuration uses `channels_redis.core.RedisChannelLayer` with `REDIS_URL` as the single source of truth.

Testing continues to use `channels.layers.InMemoryChannelLayer`.

## Pagination and Dashboard Scale

The existing DRF pagination class remains unchanged. Dashboard list endpoints keep their documented filters and ordering behavior.

Representative dashboard filtering and pagination are covered by Phase 3 tests.

## Data Integrity Improvements

Phase 3 preserves and reinforces existing integrity rules:

- Group duplicate membership prevention.
- Device token uniqueness and safe reassignment.
- Verification single-review behavior.
- Print transition consistency.
- Closed support ticket message rejection.

## Soft-Delete Review

The shared soft-delete hook now delegates to `perform_destroy`, allowing viewsets to audit deletes before soft deletion.

Normal list endpoints already filter `is_deleted=False` where soft-delete behavior is used. Phase 3 did not expand soft delete aggressively.

## Background Job Foundation

Celery is dependency-ready but no full Celery app or beat schedule is configured in this phase.

Added:

```powershell
.\.venv\Scripts\python.exe app\manage.py cleanup_expired_otp --retention-days 1
```

The command is idempotent and suitable for a future scheduled worker or platform cron.

## Health and Readiness

Existing health endpoints remain:

- `/api/v1/health/`
- `/api/v1/health/db/`

Added:

- `/api/v1/health/ready/`

Readiness checks database and cache availability using the existing response envelope and avoids leaking sensitive configuration.

## Logging and Observability

Production logging remains console-based and environment-controlled through `LOG_LEVEL`. Audit logs remain separate application records and continue to redact sensitive keys.

Request IDs, slow-query logging, and external monitoring are deferred to Phase 4.

## Tests Added

Added `app/apps/common/tests_phase3_reliability.py` covering:

- Production Redis cache settings.
- Production Channels Redis settings.
- Testing cache/channel independence.
- Migration dry-run check.
- Readiness endpoint envelope.
- Duplicate group membership handling.
- Duplicate device token handling.
- Verification single-review behavior.
- Print transition consistency.
- Closed support ticket message rejection.
- Dashboard file pagination/filtering.
- OTP cleanup command idempotency.

## Validation Commands

Run:

```powershell
.\.venv\Scripts\python.exe app\manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe -m pytest app\apps\common\tests_phase3_reliability.py
.\.venv\Scripts\python.exe -m pytest app\apps\common\tests_api_contract_collections.py
.\.venv\Scripts\python.exe -m pytest app\apps\common\tests_production_hardening.py
.\.venv\Scripts\python.exe -m pytest app\apps\common\tests_phase2_security.py
.\.venv\Scripts\python.exe -m pytest
.\scripts\validate_backend.ps1 -DeployCheck
```

## Known Limitations

- Redis readiness is configured and tested at settings level; unit tests do not connect to a real Redis server.
- True concurrent race-condition tests are not included to avoid flaky tests; deterministic sequential tests cover the same state guards.
- Full Celery worker and beat scheduling are deferred.
- Query-count assertions are avoided because they would be brittle with DRF serializers and test data shape.
- Production media/object-storage authorization still needs operational design.

## Remaining Risks for Phase 4

- Request correlation IDs and structured logging.
- Slow-query monitoring and dashboard performance profiling under real data volume.
- External monitoring integration.
- Celery app and scheduled task configuration.
- Object storage and signed/protected media delivery strategy.
- CI quality gates and test categorization.

## Next Recommended Phase

Phase 4 should focus on testing, quality gates, and observability.
