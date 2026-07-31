# Panorama Backend documentation

Owner: Backend Platform Team  
Last reviewed: 2026-07-31  
Version: API v2

This is the canonical documentation entry point. It replaces phase reports and
duplicated deployment notes that were useful during implementation but are not
safe release evidence on their own.

| Document | Purpose |
| --- | --- |
| [Architecture](ARCHITECTURE.md) | Components, boundaries, data flows, PostgreSQL, Redis, Celery, ASGI, and storage. |
| [Deployment and operations](DEPLOYMENT_AND_OPERATIONS.md) | Docker/Coolify deployment, health, services, configuration, rollback, and troubleshooting. |
| [API security and authentication](API_SECURITY_AND_AUTH.md) | API contract rules, JWT/OTP, RBAC, protected assets, and error handling. |
| [Lecture viewer and document pipeline](LECTURE_VIEWER_AND_DOCUMENT_PIPELINE.md) | Private source documents, conversion queue, viewer sessions, page delivery, and notes. |
| [Storage, backup, and recovery](STORAGE_BACKUP_AND_RECOVERY.md) | Local named volume, backup/restore, and future generic S3 migration. |
| [Quality, testing, and release](QUALITY_TESTING_AND_RELEASE.md) | Baseline, checks, test policy, and release gates. |
| [OpenAPI artifacts](api/openapi.json) | Generated API contract; the YAML and Postman collection are adjacent. |

## Documentation maintenance

Update the applicable canonical document in the same change as architectural,
operational, security, or API changes. Do not create a new root-level phase
report for a routine implementation. Evidence from CI, Staging, load tests,
DAST, backup/restore, and rollback belongs in release artifacts rather than in
these evergreen documents.
