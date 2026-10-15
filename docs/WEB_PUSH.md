# Browser Web Push (student PWA)

The student web app (a PWA) receives notifications while closed through standard Web Push with VAPID. It is a
separate channel from mobile push: browser subscriptions live in `notifications.WebPushSubscription`, Expo tokens
stay in `DeviceToken` / `DeviceInstallation`. Every notification created through `NotificationService` (verification
decisions, groups, files, support, printing, announcements, campaigns, ...) is delivered to both channels, subject to
the user's `push_enabled` and `disabled_types` preferences exactly like Expo.

## Configuration

| Variable | Meaning |
| --- | --- |
| `WEB_PUSH_VAPID_PUBLIC_KEY` | VAPID public key, base64url (65-byte uncompressed P-256 point). Served to the browser. |
| `WEB_PUSH_VAPID_PRIVATE_KEY` | VAPID private key, base64url raw 32-byte value (or PEM). Secret; never logged or returned. |
| `WEB_PUSH_VAPID_SUBJECT` | Contact URI for the push services: `mailto:ops@your-domain` or `https://your-domain`. |
| `WEB_PUSH_ALLOWED_HOST_SUFFIXES` | Optional, comma separated. Hosts a subscription endpoint may use (exact host or any subdomain). Default: `fcm.googleapis.com,updates.push.services.mozilla.com,push.apple.com,notify.windows.com`. Add a new browser vendor here; an empty value disables the allowlist (private/loopback/IP hosts stay rejected). |
| `WEB_PUSH_TIMEOUT_SECONDS` | Per-request timeout, default `5`. |
| `WEB_PUSH_MAX_CONSECUTIVE_FAILURES` | Non-retryable 4xx answers (other than 404/410) after which a subscription is deactivated, default `5`. |
| `THROTTLE_WEB_PUSH` | Rate for subscribe/unsubscribe per user, default `30/hour`. |

Web push is **configured only when all three `WEB_PUSH_VAPID_*` values are set**. Otherwise `public-key` reports
`enabled: false`, `POST subscriptions` answers `503 WEB_PUSH_NOT_CONFIGURED` and nothing is delivered.
`validate_production_env` only prints a warning when the configuration is missing or partial; it never fails a release.

### Gating versus `PUSH_NOTIFICATIONS_ENABLED`

`PUSH_NOTIFICATIONS_ENABLED` still controls Expo (mobile) only. Browser push is on whenever it is configured, even if
Expo is off. When a notification is created the enabled channels are recorded on the queued task
(`deliver_push_notification(..., channels=["expo", "web"])`), so enabling one channel never sends through the other.
Tasks queued before this change carry no channel list and behave as before.

### Generating keys (do this per environment; never commit them)

```bash
npx web-push generate-vapid-keys --json
# or, with the backend virtualenv:
python - <<'PY'
import base64
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

b64 = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
key = ec.generate_private_key(ec.SECP256R1())
print("WEB_PUSH_VAPID_PRIVATE_KEY=" + b64(key.private_numbers().private_value.to_bytes(32, "big")))
print("WEB_PUSH_VAPID_PUBLIC_KEY=" + b64(key.public_key().public_bytes(
    serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)))
PY
```

Changing the key pair invalidates existing subscriptions (the push service rejects them); clients re-subscribe
after reading the new public key, and dead subscriptions are deactivated automatically on the first 403/404/410.

## API contract

All endpoints are under `/api/v1/notifications/web-push/`, require a bearer token and use the standard envelope.

`GET public-key/`

```json
{"success": true, "code": "OK", "message": "...", "data": {"enabled": true, "public_key": "B..."}}
```

`enabled: false` gives `"public_key": null`.

`POST subscriptions/` (throttled, upsert by `endpoint`; an endpoint already owned by another account is re-assigned
to the caller and reactivated). Body is `PushSubscription.toJSON()` plus an optional `user_agent`:

```json
{"endpoint": "https://fcm.googleapis.com/fcm/send/...", "keys": {"p256dh": "<base64url>", "auth": "<base64url>"}, "user_agent": "optional"}
```

`201` with `data: {id, user_agent, is_active, last_used_at, created_at, updated_at}` (`code: WEB_PUSH_SUBSCRIBED`; the
endpoint and keys are never echoed). `400` for an endpoint that is not an https URL on an allowed push service, longer
than 1024 characters, or keys that are not base64url of the right length (p256dh 65 bytes, auth 16 bytes).
`503 WEB_PUSH_NOT_CONFIGURED` when the feature is off.

`DELETE subscriptions/` (throttled), body `{"endpoint": "..."}`: `200` (`code: WEB_PUSH_UNSUBSCRIBED`); `404` when the
endpoint is unknown or belongs to someone else.

## Push payload and service worker contract

The service worker receives JSON (already decrypted by the browser):

```json
{
  "title": "...", "body": "...", "tag": "panorama-verification-42",
  "data": {"url": "/verification", "notification_id": 42, "type": "verification", "status": "approved"}
}
```

`data.url` is always a same-origin path (falls back to `/notifications` when the notification has no usable
`deep_link`); the worker should `showNotification(title, {body, tag, data})` and on `notificationclick` open/focus
`data.url`. Subscribe with `applicationServerKey` set to the `public_key` from the endpoint above and
`userVisibleOnly: true`.

## Delivery behaviour

- 2xx: delivered, failure counter reset. 404/410: subscription deactivated. 429, 5xx, timeouts and connection
  errors: transient, only that subscription is retried with the existing exponential backoff (no duplicates to the
  ones already delivered, and Expo tokens are retried independently). Other 4xx: counted; deactivated after
  `WEB_PUSH_MAX_CONSECUTIVE_FAILURES` consecutive failures.
- SSRF: endpoints must be `https` on the default port, without credentials, never an IP literal or a local name, and
  inside the host allowlist; this is checked on subscribe and again before every send. Redirects from a push service
  are not followed.
- Endpoints, keys and the private key are never logged; the admin hides them.

## Local end-to-end

Generate a throwaway pair with the script above, then start the e2e backend with the variables in the environment
(`config.settings.e2e` inherits them from `base.py`):

```bash
cd app
export WEB_PUSH_VAPID_PUBLIC_KEY=... WEB_PUSH_VAPID_PRIVATE_KEY=... WEB_PUSH_VAPID_SUBJECT=mailto:dev@example.test
python manage.py migrate --settings=config.settings.e2e
python manage.py runserver 127.0.0.1:8000 --settings=config.settings.e2e
```
