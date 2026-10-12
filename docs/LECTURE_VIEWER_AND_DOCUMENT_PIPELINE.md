# Lecture viewer and document pipeline

Owner: Learning Platform Team  
Last reviewed: 2026-07-31

## Decision

The selected viewer model is **rendered per-page images plus protected text
layer**, rather than delivering the normalized PDF to students. This is the
more restrictive of the two evaluated options: it avoids exposing the original
document and permits page-by-page authorization. It does not and cannot prevent
screenshots, camera capture, or manual transcription.

The original source is private and accessible only to `lectures.manage` staff.
The student API never serializes an original key, direct URL, filesystem path,
or a long-lived token.

## Upload and processing

Staff upload `.pdf`, `.doc`, `.docx`, `.ppt`, or `.pptx`. Validation checks size,
extension allowlist, magic signature, Office package structure, archive entry
count/expanded-size limit, a SHA-256 digest, and duplicate subject/hash. A
future ClamAV check is opt-in and fails closed if enabled but unavailable.

The processing states are `uploaded`, `queued`, `scanning`, `converting`,
`extracting`, `rendering`, `ready`, `failed`, and `quarantined`. Students can
only access published `ready` lectures. The `conversion` Celery queue uses the
`conversion-runtime` Docker target, containing LibreOffice Writer/Impress,
Poppler, and fonts; the web image intentionally remains free of those tools.

LibreOffice is called with a list of arguments, a random temporary workspace,
per-job user profile, timeout, and `shell=False`. Output must exist, be nonempty,
and parse as PDF. Temporary input/output exists only for the task lifetime;
durable output is saved through Django storage. Run
`python manage.py document_pipeline_status` to inspect capability availability
without converting a file.

## Viewer routes

| Route | Purpose |
| --- | --- |
| `GET /api/v1/lectures/{id}/viewer/manifest/` | Safe title/page/capability manifest. |
| `POST /api/v1/lectures/{id}/viewer/session/` | Creates an authenticated short-lived session. |
| `GET /api/v1/lectures/{id}/viewer/pages/{n}/` | Protected inline page image; requires `X-Viewer-Session`. |
| `GET /api/v1/lectures/{id}/viewer/pages/{n}/thumbnail/` | Protected thumbnail. |
| `GET /api/v1/lectures/{id}/viewer/pages/{n}/text/` | Protected searchable text layer. |
| `GET /api/v1/lectures/{id}/notes/` | Lists only the caller's unarchived notes. |

Each page/text/thumbnail request validates the user, active student profile,
matching major/year/semester, ready/published state, short-lived session, and
per-user rate limit. Viewer responses are inline and `private, no-store`.

## Notes

Notes are personal records scoped to `(student, lecture)`. They support optional
page anchors, sanitized selected text/content, bookmarks, favourites, an
idempotency key for autosave, and optimistic version checking. A stale update
returns HTTP 409. Staff do not read student notes by default.

## Conversion sandbox and residual risk

LibreOffice, `pdftoppm` and `clamscan` run through `apps/lectures/sandbox.py`:
scrubbed environment (only `PATH`, `LANG`, and `HOME` pointing at a per-job
temp dir, so no `SECRET_KEY`, database, Redis or encryption keys), a private
per-job working dir and LibreOffice profile deleted afterwards, POSIX rlimits
(`RLIMIT_CPU`, `RLIMIT_AS`, `RLIMIT_NOFILE`, `RLIMIT_FSIZE`; skipped on
Windows), a hard wall-clock timeout that kills the whole process group, and
capped captured/produced output. Tunables: `DOCUMENT_CONVERSION_TIME_LIMIT`,
`DOCUMENT_CONVERSION_CPU_LIMIT`, `DOCUMENT_CONVERSION_MEMORY_LIMIT_MB`,
`DOCUMENT_CONVERSION_MAX_OPEN_FILES`, `DOCUMENT_CONVERSION_MAX_OUTPUT_BYTES`.

Residual risk: this is application-level hardening only. The converter still
shares the container's network namespace (no network isolation) and can read any
file the app user can read (for example the media volume). A compromised
converter could therefore reach internal services or exfiltrate readable data.
Full isolation requires container-level controls: run conversion in a dedicated
worker/container with no network (or egress deny), a read-only root filesystem,
a non-root user, no mounted secrets, and only the job's input/output mounted.
