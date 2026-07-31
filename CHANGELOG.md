# Changelog

## 2026-07-31 — Lecture viewer platform

- Added additive API v2 lecture routes, private originals, conversion states,
  protected viewer sessions/pages/text/thumbnails, and private student notes.
- Added a dedicated conversion-worker Docker target and a safe capability status
  command.
- Added Redis timeout/retry configuration, conversion queue routing, and lecture
  throttle settings.
- Consolidated evergreen documentation under `docs/`.

No existing API path or response field was removed in this change.
