# ASVS Level 2 final matrix

`docs/security/ASVS_L2_MATRIX.md` contains the implementation-oriented Phase 1 matrix. This final gate tracks release evidence rather than converting unexecuted controls into a pass.

| Control area | Repository evidence | Release evidence required | Status |
| --- | --- | --- | --- |
| Authentication/session | JWT rotation and blacklist configuration; smoke route | Staging JWT replay/refresh test | BLOCKED |
| Authorization/API | Role permissions and regression tests | Authenticated IDOR/mass-assignment DAST evidence | BLOCKED |
| Cryptography/secrets | Fernet validation, Coolify matrix, secret scan workflow | Key custody and rotation drill | BLOCKED |
| File handling | Private S3 configuration and protected ticket routes | Private bucket/CORS policy and upload-polyglot test | BLOCKED |
| Logging/monitoring | JSON request logging | Central log, alert, and PII-scrub verification | BLOCKED |
| Deployment | Non-root image, release job, immutable workflow | Coolify deployment/rollback evidence | BLOCKED |
| Availability | live/ready/startup endpoints, backup scripts | Staging load, restore, and Redis failure drill | BLOCKED |

Conclusion: no Critical/High vulnerability claim is made because ASVS validation and DAST have not executed in Staging.
