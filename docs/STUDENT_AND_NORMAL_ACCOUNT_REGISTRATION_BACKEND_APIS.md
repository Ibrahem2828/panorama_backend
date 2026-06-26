# Student and Normal Account Registration — Backend APIs

## Final Business Rule

| Account type | OTP timing | Who receives OTP |
|--------------|------------|-------------------|
| **Normal user** | Immediately after `POST /api/v1/auth/register/normal/` | User verifies via `verify_phone` (existing OTP flow). OTP is never returned to mobile in production (`RETURN_DEVELOPMENT_OTP=False`). |
| **Student** | Only after admin/dashboard approval | Dashboard approve/resend response returns OTP for staff to copy and send manually via WhatsApp. Mobile never receives OTP. |

No WhatsApp API integration exists in this phase.

---

## Phone number format

Official backend storage and response format is **E.164**.

For Syrian mobile numbers, the canonical format is:

```text
+9639XXXXXXXX
```

Accepted mobile input formats:

| Client input | Stored/returned value |
|--------------|-----------------------|
| `0994109259` | `+963994109259` |
| `963994109259` | `+963994109259` |
| `+963994109259` | `+963994109259` |

Rejected examples include `994109259`, `00963994109259`, numbers with letters, landline formats, and numbers that are too short or too long.

Mobile may send `0994109259` or `+963994109259`. Backend normalizes the value before storing, duplicate checks, OTP generation, OTP verification, and response payloads.

Recommended mobile helper text:

```text
مثال: 0994109259 أو +963994109259
```

Invalid phone response:

```json
{
  "success": false,
  "message": "تحقق من البيانات المدخلة وحاول مرة أخرى.",
  "errors": {
    "phone_number": [
      "صيغة رقم الجوال غير صحيحة. استخدم مثالاً مثل: +963994109259 أو 0994109259."
    ]
  },
  "data": {
    "error_code": "invalid_phone",
    "expected_phone_format": "E.164",
    "examples": ["+963994109259", "0994109259"]
  },
  "request_id": "..."
}
```

Duplicate phone response:

```json
{
  "success": false,
  "message": "رقم الجوال مستخدم مسبقاً.",
  "errors": {
    "phone_number": ["رقم الجوال مستخدم مسبقاً."]
  },
  "data": {
    "error_code": "duplicate_phone"
  },
  "request_id": "..."
}
```

Duplicate email response:

```json
{
  "success": false,
  "message": "البريد الإلكتروني مستخدم مسبقاً.",
  "errors": {
    "email": ["البريد الإلكتروني مستخدم مسبقاً."]
  },
  "data": {
    "error_code": "duplicate_email"
  },
  "request_id": "..."
}
```

---

## Rate limiting

Sensitive public auth endpoints remain throttled. Normal user registration uses an endpoint-specific policy:

| Endpoint | Policy |
|----------|--------|
| `POST /api/v1/auth/register/normal/` | 7 attempts / 20 minutes |
| `POST /api/v1/auth/otp/verify/` and `POST /api/v1/auth/verify-phone/` | 7 attempts / 20 minutes plus OTP max-attempt protection |
| `POST /api/v1/auth/student-account-requests/` | 7 attempts / 20 minutes |
| `POST /api/v1/auth/student-account-requests/{request_id}/verify-otp/` | 7 attempts / 20 minutes plus OTP max-attempt protection |

DRF throttling counts allowed requests before serializer validation, so successful, invalid, duplicate, and repeated registration attempts all count toward the normal-register window. Throttle keys include IP and a hashed request identifier where available; phone identifiers are normalized before hashing.

429 response:

```json
{
  "success": false,
  "message": "تم تجاوز عدد المحاولات المسموح. حاول مرة أخرى بعد 20 دقيقة.",
  "errors": {
    "detail": "تم تجاوز عدد المحاولات المسموح. حاول مرة أخرى بعد 20 دقيقة."
  },
  "data": {
    "error_code": "rate_limited",
    "retry_after_seconds": 1200,
    "retry_after_minutes": 20
  },
  "request_id": "..."
}
```

The `Retry-After` header is preserved. Mobile should display:

```text
تم تجاوز عدد المحاولات المسموح. حاول مرة أخرى بعد X دقيقة.
```

---

## Normal User Flow

```
register/normal → phone OTP sent → verify_phone → login
```

1. `POST /api/v1/auth/register/normal/` creates an active `normal_user` with `is_phone_verified=false`.
2. Backend immediately sends `verify_phone` OTP via existing `OTPService`.
3. User verifies with `POST /api/v1/auth/otp/verify/` or `POST /api/v1/auth/verify-phone/`.
4. On success, `is_phone_verified=true`.
5. User logs in with `POST /api/v1/auth/login/`.

**Note:** Login is allowed before phone verification in the current system (`is_active=true` at registration). Mobile should use `requires_phone_verification` on register/login/`/me` to route users to the OTP screen without breaking existing login behavior.

### Register normal user

`POST /api/v1/auth/register/normal/`

```json
{
  "full_name": "إبراهيم محمد خير سعد الدين",
  "email": "ibrahemsa28@example.com",
  "phone_number": "0994109259",
  "password": "StrongPass123!",
  "password_confirm": "StrongPass123!"
}
```

Response:

```json
{
  "success": true,
  "message": "تم إنشاء الحساب بنجاح. أرسلنا رمز تحقق إلى رقم الجوال لتفعيل الحساب.",
  "data": {
    "user": {
      "id": 1,
      "role": "normal_user",
      "is_phone_verified": false,
      "phone_verified": false,
      "requires_phone_verification": true
    },
    "requires_otp": true,
    "otp_purpose": "verify_phone",
    "phone_number": "+963994109259",
    "phone_number_masked": "+963******259",
    "phone_verified": false,
    "requires_phone_verification": true,
    "next_step": "verify_phone",
    "expires_in_seconds": 600,
    "resend_after_seconds": 60
  }
}
```

`development_otp` is returned only when `RETURN_DEVELOPMENT_OTP=True` (local/testing). Never in production.

### Verify phone (alias)

`POST /api/v1/auth/verify-phone/`

```json
{
  "phone_number": "0994109259",
  "code": "123456"
}
```

`purpose` defaults to `verify_phone` if omitted.

Success response:

```json
{
  "success": true,
  "message": "تم التحقق من رقم الجوال بنجاح. يمكنك تسجيل الدخول الآن.",
  "data": {
    "phone_verified": true,
    "requires_phone_verification": false,
    "is_phone_verified": true,
    "next_step": "login"
  }
}
```

### Login and /me phone verification flags

Login response `user` object and `GET /api/v1/auth/me/` include:

- `phone_verified` — alias of `is_phone_verified`
- `requires_phone_verification` — `true` only for `normal_user` with unverified phone

---

## Student Flow

```
student-account-requests → admin review → dashboard OTP → mobile verify-otp → login
```

1. `POST /api/v1/auth/student-account-requests/` creates `StudentAccountRequest` with `pending_review`. No User, no OTP.
2. Admin reviews card via dashboard + protected preview token.
3. `POST /api/v1/dashboard/student-account-requests/{id}/approve/` generates OTP and returns it to dashboard only.
4. Staff copies `manual_whatsapp_message` and sends via personal WhatsApp.
5. Student enters OTP in mobile: `POST /api/v1/auth/student-account-requests/{request_id}/verify-otp/`.
6. Backend creates `User` (role=student) + `StudentProfile` (`verification_status=approved`).
7. Student logs in normally.

### Submit student account request

`POST /api/v1/auth/student-account-requests/` (multipart/form-data)

Fields: `full_name`, `email`, `phone_number`, `university`, `faculty` (optional), `major` (optional), `student_number`, `password`, `password_confirm`, `uploaded_card`

Response:

```json
{
  "success": true,
  "message": "تم إرسال طلب إنشاء حساب الطالب بنجاح. سيتم مراجعة بياناتك من قبل الإدارة.",
  "data": {
    "request_id": "uuid",
    "status": "pending_review",
    "next_step": "admin_review"
  }
}
```

### Check request status

`GET /api/v1/auth/student-account-requests/{request_id}/status/?phone_number=+963...`

Optional `phone_number` query param prevents enumeration if request_id is leaked.

Response:

```json
{
  "success": true,
  "data": {
    "request_id": "uuid",
    "status": "otp_sent",
    "public_message": "تم قبول طلبك. يرجى إدخال رمز التفعيل المرسل إليك.",
    "can_enter_otp": true,
    "can_resubmit": false
  }
}
```

### Verify student OTP

`POST /api/v1/auth/student-account-requests/{request_id}/verify-otp/`

```json
{ "code": "123456" }
```

Response:

```json
{
  "success": true,
  "message": "تم تفعيل حساب الطالب بنجاح. يمكنك تسجيل الدخول الآن.",
  "data": { "status": "active", "next_step": "login" }
}
```

---

## Dashboard APIs

| Method | Path | Permission |
|--------|------|------------|
| GET | `/api/v1/dashboard/student-account-requests/` | admin, it_support |
| GET | `/api/v1/dashboard/student-account-requests/{id}/` | admin, it_support |
| POST | `/api/v1/dashboard/student-account-requests/{id}/approve/` | admin, it_support |
| POST | `/api/v1/dashboard/student-account-requests/{id}/reject/` | admin, it_support |
| POST | `/api/v1/dashboard/student-account-requests/{id}/needs-update/` | admin, it_support |
| POST | `/api/v1/dashboard/student-account-requests/{id}/resend-otp/` | admin, it_support |
| POST | `/api/v1/dashboard/student-account-requests/{id}/card-preview-token/` | admin, it_support |

`print_staff` receives 403 on all review endpoints.

### Approve response (OTP for manual WhatsApp)

```json
{
  "success": true,
  "message": "تم قبول الطلب وتوليد رمز التفعيل.",
  "data": {
    "request_id": "uuid",
    "status": "otp_sent",
    "otp_code": "123456",
    "otp_expires_at": "2026-06-22T12:10:00Z",
    "resend_after_seconds": 60,
    "whatsapp_phone": "+963900000010",
    "manual_whatsapp_message": "رمز تفعيل حسابك في بانوراما هو: 123456. الرمز صالح لمدة 10 دقائق."
  }
}
```

---

## Status Values

| Status | Meaning |
|--------|---------|
| `pending_review` | Awaiting admin review |
| `approved_pending_otp` | Approved, OTP being prepared |
| `otp_sent` | OTP generated, awaiting student entry |
| `active` | Account activated |
| `rejected` | Rejected by admin |
| `needs_update` | Admin requested data update (status lookup only this phase) |
| `expired` | Reserved for future expiry cleanup (not auto-set in this phase) |

---

## Error Examples

Wrong OTP (normal or student):

```json
{
  "success": false,
  "message": "Validation error",
  "errors": { "code": ["Invalid OTP code."] }
}
```

Expired OTP:

```json
{
  "success": false,
  "errors": { "code": ["OTP code has expired."] }
}
```

Max attempts:

```json
{
  "success": false,
  "errors": { "code": ["Maximum OTP verification attempts exceeded."] }
}
```

Resend cooldown (dashboard):

```json
{
  "success": false,
  "message": "Please wait 45 seconds before resending OTP."
}
```

Dashboard forbidden (`print_staff`):

```json
{
  "success": false,
  "message": "You do not have permission to perform this action."
}
```

---

## OTP Security Controls

| Rule | Value |
|------|-------|
| Length | 6 digits |
| Expiry | 10 minutes |
| Max attempts | 5 (`MAX_OTP_VERIFY_ATTEMPTS`) |
| Resend cooldown | 60 seconds between resend calls (`STUDENT_ACCOUNT_OTP_RESEND_COOLDOWN_SECONDS`) |
| Storage | Hashed (`make_password`) for student request OTP; existing `OTPCode` for normal user |
| Logging | OTP never logged; audit sanitizer redacts `otp`, `code`, `password` keys |
| Mobile exposure | Never for student OTP |
| Production dev OTP | `RETURN_DEVELOPMENT_OTP=False` in production |

---

## Permissions Summary

| Actor | Normal register | Verify phone | Student request | Status | Verify student OTP | Dashboard review |
|-------|----------------|--------------|-----------------|--------|-------------------|------------------|
| Public | Yes | Yes | Yes | Yes | Yes | No |
| normal_user | — | — | — | — | — | No |
| student | — | — | — | — | — | No |
| admin / it_support | — | — | — | — | — | Yes |
| print_staff | — | — | — | — | — | No |

---

## Mobile API List

1. `POST /api/v1/auth/register/normal/`
2. `POST /api/v1/auth/verify-phone/` (alias: `POST /api/v1/auth/otp/verify/`)
3. `POST /api/v1/auth/student-account-requests/`
4. `GET /api/v1/auth/student-account-requests/{uuid}/status/`
5. `POST /api/v1/auth/student-account-requests/{uuid}/verify-otp/`

## Dashboard API List

1. `GET /api/v1/dashboard/student-account-requests/`
2. `GET /api/v1/dashboard/student-account-requests/{id}/`
3. `POST /api/v1/dashboard/student-account-requests/{id}/approve/`
4. `POST /api/v1/dashboard/student-account-requests/{id}/reject/`
5. `POST /api/v1/dashboard/student-account-requests/{id}/needs-update/`
6. `POST /api/v1/dashboard/student-account-requests/{id}/resend-otp/`
7. `POST /api/v1/dashboard/student-account-requests/{id}/card-preview-token/`

## Mobile Integration Notes

1. Use `register/normal` then `verify-phone` for normal users.
2. After register or login, if `user.requires_phone_verification=true`, route to OTP screen.
3. Use `student-account-requests` flow for new students (replaces direct `register/student` when mobile migrates).
4. Poll `status` endpoint; show OTP entry when `can_enter_otp=true`.
5. Legacy `register/student` and verification submit flow remain available for backward compatibility.

## Dashboard Integration Notes

1. Build list/detail UI for student account requests.
2. On approve, display `otp_code` and `manual_whatsapp_message` with copy button.
3. Use `card-preview-token` + protected media for card review.
4. Resend OTP respects 60s cooldown after first resend.

## Known Limitations

- No mobile resubmit after `needs_update` (status lookup only).
- No WhatsApp API sending.
- Legacy `register/student` still creates immediate user (unchanged).

## Deployment

- Run migrations: `0004_studentaccountrequest`, `0005_studentaccountrequest_otp_resend_count`
- No new required env vars
- Optional: `THROTTLE_NORMAL_REGISTER`, `THROTTLE_OTP_VERIFY`, `THROTTLE_STUDENT_ACCOUNT_REQUEST`, `THROTTLE_STUDENT_ACCOUNT_REQUEST_OTP_VERIFY`, `STUDENT_ACCOUNT_OTP_RESEND_COOLDOWN_SECONDS`
- Media uploads stored under `student_account_requests/`
- After a throttle policy change, old Redis throttle keys may still block requests until their original expiry unless targeted throttle keys are cleared. A backend restart alone does not clear Redis cache. If targeted clearing is not performed, QA should wait until the old throttle window expires before retesting the same IP/identifier.
