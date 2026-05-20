# Panorama Mobile Frontend Integration

## Overview

- Base URL: `API_BASE_URL`, for example `http://localhost:8000`
- API prefix: `/api/v1/`
- WebSocket base: `WS_BASE_URL`, for example `ws://localhost:8000`
- Auth: `Authorization: Bearer <access_token>`
- API collection: `docs/api/mobile_api_collection.json`

Success response:

```json
{"success": true, "message": "Operation completed successfully", "data": {}}
```

Error response:

```json
{"success": false, "message": "Validation error", "errors": {}}
```

Paginated lists return `data.count`, `data.next`, `data.previous`, and `data.results`.

Media fields return URLs/paths from the backend. Treat card images and protected files as authenticated resources; never assume a file is accessible until the API returns it.

## Environment

- `API_BASE_URL`
- `WS_BASE_URL`

## Authentication Flow

1. Normal users call `POST /api/v1/auth/register/normal/`.
2. Students call `POST /api/v1/auth/register/student/`.
3. Send or verify OTP with `/api/v1/auth/otp/send/` and `/api/v1/auth/otp/verify/`.
4. Login with `POST /api/v1/auth/login/`.
5. Store `access` and `refresh` securely.
6. On 401, call `/api/v1/auth/token/refresh/`, retry the failed request once, then logout if refresh fails.
7. Logout with `/api/v1/auth/logout/`.

## Student Onboarding

1. Register student.
2. Parse number with `GET /api/v1/students/student-number/parse/?student_number=2150094`.
3. Update `/api/v1/students/me/profile/`.
4. Submit verification with `/api/v1/verification/submit/`.
5. Poll or open `/api/v1/verification/me/`.
6. Handle statuses: `pending`, `approved`, `rejected`, `needs_update`.
7. Before approval, show limited student features. After approval, enable groups, restricted files, and academic chat.

Approved students cannot edit student number or sensitive academic fields from mobile APIs.

## Academic Data

Use these to build profile and filters:

- `/api/v1/universities/`
- `/api/v1/universities/{id}/faculties/`
- `/api/v1/faculties/{id}/majors/`
- `/api/v1/academic-years/`
- `/api/v1/semesters/`
- `/api/v1/majors/{id}/subjects/`

## Home

Call `/api/v1/announcements/` after login. The backend returns only relevant active announcements.

## Groups

- Available groups: `/api/v1/groups/available/`
- My groups: `/api/v1/groups/my/`
- Group detail: `/api/v1/groups/{id}/`
- Join: `/api/v1/groups/{id}/join/`
- Leave: `/api/v1/groups/{id}/leave/`

Group responses include `image`, `send_messages_permission`, `current_user_membership_status`, `current_user_group_role`, and `members_count`.

For `send_messages_permission`:

- `all_members`: approved members can send.
- `admins_only`: only `moderator`, `group_admin`, Admin, or IT can send.

The frontend should hide or disable the input based on these fields, but the backend is the source of truth and will reject unauthorized sends.

## Chat

Load previous messages:

```text
GET /api/v1/groups/{group_id}/messages/
```

Connect:

```text
ws://host/ws/v1/groups/{group_id}/chat/?token=<access_token>
```

Send text:

```json
{"type": "message", "content": "Hello"}
```

Typing:

```json
{"type": "typing", "is_typing": true}
```

Received message:

```json
{"type": "message", "data": {"id": 123, "content": "Hello", "message_type": "text"}}
```

If token expires, close the socket, refresh token, reconnect. Do not start with chat first: chat depends on auth, verified student profile, group eligibility, and approved membership.

## Files

- All accessible files: `/api/v1/files/`
- File detail: `/api/v1/files/{id}/`
- Group files: `/api/v1/groups/{id}/files/`

Visibility is enforced server-side.

## Printing

- Create order: `POST /api/v1/printing/orders/`
- My orders: `GET /api/v1/printing/orders/my/`
- Detail: `GET /api/v1/printing/orders/{id}/`
- Cancel: `POST /api/v1/printing/orders/{id}/cancel/`

Orders can use uploaded files or existing `source_file` IDs. Display `priority`, `status`, and `status_history` when present.

## Notifications

- List: `/api/v1/notifications/`
- Unread count: `/api/v1/notifications/unread-count/`
- Mark read: `/api/v1/notifications/{id}/read/`
- Read all: `/api/v1/notifications/read-all/`
- Register device token: `/api/v1/notifications/device-tokens/`

Register FCM/APNs token after login and whenever token changes.

## Support

- Create ticket: `/api/v1/support/tickets/`
- My tickets: `/api/v1/support/tickets/my/`
- Detail: `/api/v1/support/tickets/{id}/`
- Add message: `/api/v1/support/tickets/{id}/messages/`

Closed/resolved tickets reject new messages.

## Error Handling

- 400: show validation messages from `errors`.
- 401: refresh access token once.
- 403: user lacks role/status/membership.
- 404: resource does not exist or is not owned by the user.
- WebSocket close `4403`: access denied.

## Recommended Build Order

1. Auth
2. Student Profile
3. Verification
4. Home/Announcements
5. Groups
6. Files
7. Printing
8. Chat
9. Support
10. Notifications

## Complete Student Scenario

Login, verify phone, complete student profile, submit verification, wait for approval, join a group, open chat, print a group file, track order status, and contact support if needed.
