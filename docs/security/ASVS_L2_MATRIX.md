# ASVS L2 Control Matrix

This is an implementation and verification map, not an assertion that a
production security assessment has passed. `Verified` means the named
automated check has been run in this repository; `Operational` requires a
staging or production exercise outside this checkout.

| ASVS area | Backend control | Evidence | Status |
| --- | --- | --- | --- |
| V1 Architecture | Deny-by-default DRF permissions, uniform response envelope and request ID | `app/config/settings/base.py`, `apps/common/*` | Verified by Django checks/tests |
| V2 Authentication | Hashed OTP, expiry, cooldown, attempt limit, refresh-token rotation | `apps/accounts/services.py`, `tests/test_otp_and_login.py` | Verified by tests |
| V3 Session management | JWT rotation/blacklist and logout revocation | `config/settings/base.py`, accounts serializers/tests | Verified by tests |
| V4 Access control | Resource-scoped querysets, group membership checks, dashboard capabilities | `apps/files/services.py`, `apps/groups/services.py`, phase tests | Verified by tests |
| V5 Validation | Server-side serializers, file signature/type/size checks | `apps/common/file_validation.py`, serializers | Verified by tests |
| V6 Cryptography | Required field-encryption key and private S3/R2 signed storage in production | `config/settings/production.py` | Verified by production-settings tests |
| V7 Error handling | Sanitised exception envelope, no internal 5xx details | `apps/common/exceptions.py` | Verified by tests |
| V8 Data protection | Expiring single-purpose access tickets consumed under row locks; no raw file URL fields | files/printing/verification/support models and views | Verified by contract tests |
| V9 Communications | TLS/HSTS/security headers in production configuration | `config/settings/production.py`, middleware | Configuration verified; staging required |
| V10 Malicious code | Upload type/signature checks; no malware engine is configured in this checkout | `apps/common/file_validation.py` | Operational gap: bind ClamAV scanner before release |
| V11 Business logic | Print status state machine, backend price snapshot/revision and idempotency key | `apps/printing/services.py`, phase tests | Verified by tests |
| V12 Files | Private object storage mandatory in production; protected stream cache headers | files/printing/support/verification views | Verified by tests/configuration |
| V13 API | OpenAPI validation, generated Postman collection, CI contract gate | `docs/api/openapi.json`, `.github/workflows/ci.yml` | Verified locally |
| V14 Configuration | Production fails fast for required secrets, DB, Redis, origins, mail, S3 | `config/settings/production.py` | Verified by tests |
| V15 Logging | Audit trail and sensitive-field redaction | `apps/audit/services.py`, security tests | Verified by tests |
| V16 Security operations | dependency audit, secrets scan, Docker build configured in CI | `.github/workflows/ci.yml` | CI execution pending remote run |

Open operational items: rotate potentially exposed secrets, remove the local
`media/` payload after explicit owner approval, configure malware scanning, and
run staging restore/load/penetration tests.
