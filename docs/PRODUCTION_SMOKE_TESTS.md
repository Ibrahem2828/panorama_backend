# Production Smoke Test Checklist

Run these checks after deploy and after rollback. Use real production-safe test accounts, not committed credentials.

- `/api/v1/health/` returns 200 and includes `X-Request-ID`.
- `/api/v1/health/ready/` returns 200.
- `/api/schema/` generates if schema access is exposed.
- `/api/docs/` loads if docs are exposed.
- Login succeeds.
- Token refresh succeeds.
- Current user endpoint returns the logged-in user.
- A mobile protected endpoint rejects unauthenticated access and works when authenticated.
- A dashboard protected endpoint rejects non-staff access and works for staff/admin.
- Student verification request path works for an eligible student.
- File list/detail works.
- Protected file view enforces authorization.
- Print order create/list works.
- Dashboard print order list works for staff.
- Support ticket create/list works.
- Group list/detail works.
- Chat REST message create/list works.
- WebSocket connects over WSS to `ws/v1/groups/<group_id>/chat/` when available.
- Notification unread count works.
- Audit log access works for admin/IT and rejects unauthorized users.
- Response headers include `X-Request-ID`.
- Logs include the matching request ID.
- Logs do not contain secrets, tokens, passwords, OTP codes, cookies, authorization headers, or raw uploaded-file paths.
