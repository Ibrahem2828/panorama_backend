# Panorama Backend v2 — Production Candidate

> هذه الحزمة نسخة مطورة أمنيًا ووظيفيًا من MVP. راجع `docs/architecture/BACKEND_V2_PRODUCTION_TRANSFORMATION_AR.md` و`docs/operations/SECURITY_AND_PRODUCTION_OPERATIONS_AR.md` قبل النشر. لا تُعتبر جاهزية الإنتاج نهائية قبل نجاح CI وStaging واختبارات الاختراق والضغط والاستعادة.

# Panorama Backend

Panorama is a Django REST backend for a student services mobile application and future admin dashboard. The backend is MVP v1 feature-complete for React Native and dashboard integration. Phase 1 provides the foundation, Phase 2 adds the academic platform, Phase 3 adds printing/chat/support/audit/stats, and the final hardening pass adds seed data, API collections, integration docs, and group messaging controls.

## Tech Stack

- Python, Django, Django REST Framework
- PostgreSQL
- JWT authentication with SimpleJWT
- Redis-ready and Celery-ready settings
- Django Channels with Redis channel layer
- drf-spectacular Swagger/OpenAPI documentation
- pytest and pytest-django
- Docker and docker-compose

## Setup With Docker

```bash
cp .env.example .env
docker compose up --build
```

The API will run at `http://localhost:8000`.

Run commands inside the web container:

```bash
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
docker compose exec web pytest
```

## Setup Without Docker

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements/local.txt
cp .env.example .env
```

Set `DB_HOST=localhost` in `.env`, make sure PostgreSQL is running, then:

```bash
cd app
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

## Environment Variables

See `.env.example` for the full list:

- `SECRET_KEY`
- `DEBUG`
- `ALLOWED_HOSTS`
- `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`
- `CORS_ALLOWED_ORIGINS`
- `JWT_ACCESS_TOKEN_LIFETIME_MINUTES`
- `JWT_REFRESH_TOKEN_LIFETIME_DAYS`
- `REDIS_URL`
- `FCM_SERVER_KEY`

## Tests

```bash
pytest
```

The testing settings use an in-memory SQLite database for fast local feedback. The default application settings use PostgreSQL.

## API Documentation

- Swagger UI: `http://localhost:8000/api/docs/`
- OpenAPI schema: `http://localhost:8000/api/schema/`
- Mobile API JSON: `docs/api/mobile_api_collection.json`
- Dashboard API JSON: `docs/api/dashboard_api_collection.json`
- Mobile integration guide: `FRONTEND_INTEGRATION.md`
- Dashboard integration guide: `DASHBOARD_INTEGRATION.md`

## Seed Initial Data

Run after migrations:

```bash
cd app
python manage.py seed_initial_data
```

The command is idempotent and creates SPU academic data, faculties with student-number codes `1` to `7`, demo subjects, demo users, groups, files, announcements, print orders, and a support ticket.

The seed command creates non-production demo accounts with generated,
non-disclosed passwords. It must never be used to bootstrap a production
administrator; use `setup_admin_accounts` with deployment-secret environment
variables instead.

## Phase 1 Features

- Split settings: base, local, production, testing
- Custom `accounts.User` model with roles
- Student profile placeholder
- Normal and student registration
- JWT login, refresh, logout
- Current user profile and profile update
- Change password
- OTP base system with hashed OTP codes
- Phone verification and password reset base flow
- Role permissions for Phase 2
- Unified API response format and DRF exception handler
- Standard pagination
- Swagger/OpenAPI setup
- Docker, PostgreSQL, Redis
- Authentication test coverage

## Phase 2 Features

- Academic structure: universities, faculties, majors, academic years, semesters, and subjects
- Expanded student academic profile linked to academic data
- Student verification submit/resubmit and Admin/IT review workflow
- Verification approval, rejection, and needs-update notifications
- In-app notification model and user APIs
- Academic groups without realtime chat
- Group membership request, approval, rejection, block, and leave flows
- Files library with access control by role, verification status, major, and group membership
- Announcements with role, date, active-state, and academic targeting
- Dashboard CRUD APIs under `/api/v1/dashboard/`
- Django admin configuration for Phase 2 models

## Phase 2 API Groups

- Academic: `/api/v1/universities/`, `/api/v1/academic-years/`, `/api/v1/semesters/`
- Student profile: `/api/v1/students/me/profile/`
- Verification: `/api/v1/verification/submit/`, `/api/v1/verification/me/`
- Groups: `/api/v1/groups/available/`, `/api/v1/groups/my/`
- Files: `/api/v1/files/`, `/api/v1/groups/{id}/files/`
- Announcements: `/api/v1/announcements/`
- Notifications: `/api/v1/notifications/`
- Dashboard: `/api/v1/dashboard/universities/`, `/api/v1/dashboard/verifications/`, `/api/v1/dashboard/groups/`, `/api/v1/dashboard/files/`, `/api/v1/dashboard/announcements/`

## Sample Phase 2 Flow

1. Admin creates university, faculty, major, academic year, semester, and subject.
2. Student registers and verifies phone with the Phase 1 OTP flow.
3. Student updates `/api/v1/students/me/profile/`.
4. Student submits verification with a university card image.
5. Admin or IT approves the verification request.
6. Admin creates an academic group.
7. Verified student requests to join the group.
8. Admin approves membership.
9. Admin uploads public, academic, or group-restricted files.
10. Student lists relevant announcements, notifications, groups, and files.

## Creating Academic Structure

Use the dashboard APIs as an Admin or IT Support user:

```bash
POST /api/v1/dashboard/universities/
POST /api/v1/dashboard/faculties/
POST /api/v1/dashboard/majors/
POST /api/v1/dashboard/academic-years/
POST /api/v1/dashboard/semesters/
POST /api/v1/dashboard/subjects/
```

The same models are available in Django admin.

## Phase 3 Completed

## Phase 3 Features

- Student number parser for `FYYSSSS` numbers, including faculty code, enrollment year, and serial number
- Automatic parsed student-number fields on registration/profile updates
- Verification validation that rejects numeric faculty mismatches
- Printing orders with items, uploaded files, source files, status transitions, status history, and notifications
- Print staff dashboard APIs under `/api/v1/dashboard/printing/orders/`
- Group chat REST APIs and WebSocket endpoint
- Support tickets with user and dashboard workflows
- Advanced audit logs for critical actions
- Device token APIs for push notification readiness
- Dashboard stats endpoint
- Channels-ready ASGI setup using Redis in normal runtime and in-memory channels in tests
- Production settings with configurable SSL redirect and secure cookies

## Final Hardening Features

- Group image and metadata are exposed in group serializers.
- Group `send_messages_permission` supports `all_members` and `admins_only`.
- REST chat and WebSocket chat both enforce send permissions.
- Dashboard can update membership roles: `member`, `moderator`, `group_admin`.
- Print staff access remains limited to printing dashboard endpoints.
- Seed command creates repeatable demo data.
- API JSON files and integration guides are included for frontend teams.

## Student Number Format

Student numbers use `FYYSSSS`.

- `F`: faculty code, `1` to `7`
- `YY`: enrollment year code, interpreted as `2000 + YY` for MVP
- `SSSS`: serial number

Example: `2150094` means faculty code `2`, enrollment year `2015`, serial `0094`.

Numeric faculty codes:

- `1`: Human Medicine
- `2`: Dentistry
- `3`: Pharmacy
- `4`: Informatics Engineering
- `5`: Petroleum Engineering
- `6`: Business Administration
- `7`: Construction Technology Engineering

Parser endpoint:

```bash
GET /api/v1/students/student-number/parse/?student_number=2150094
```

## Printing Flow

1. User creates an order at `POST /api/v1/printing/orders/`.
2. Items can use an uploaded file or an accessible `FileResource`.
3. Verified students receive `student_priority`.
4. Print staff/Admin/IT update status at `/api/v1/dashboard/printing/orders/{id}/status/`.
5. Every status change creates history, notification, and audit log entries.

## Group Chat

REST:

```bash
GET  /api/v1/groups/{group_id}/messages/
POST /api/v1/groups/{group_id}/messages/
DELETE /api/v1/groups/{group_id}/messages/{message_id}/
POST /api/v1/groups/{group_id}/messages/{message_id}/report/
```

WebSocket:

```text
ws://localhost:8000/ws/v1/groups/{group_id}/chat/?token=<access_token>
```

Only approved verified students can read group messages. Sending also depends on `send_messages_permission`. Admin/IT can moderate. Moderators and group admins can delete inappropriate messages.

## Support Flow

Users create and manage their own tickets:

```bash
POST /api/v1/support/tickets/
GET  /api/v1/support/tickets/my/
POST /api/v1/support/tickets/{id}/messages/
```

Admin/IT dashboard:

```bash
GET   /api/v1/dashboard/support/tickets/
PATCH /api/v1/dashboard/support/tickets/{id}/status/
PATCH /api/v1/dashboard/support/tickets/{id}/priority/
POST  /api/v1/dashboard/support/tickets/{id}/assign/
POST  /api/v1/dashboard/support/tickets/{id}/messages/
```

## Audit, Push, And Stats

- Audit logs: `GET /api/v1/dashboard/audit-logs/`
- Device tokens: `POST /api/v1/notifications/device-tokens/`
- Dashboard stats: `GET /api/v1/dashboard/stats/`

Push notification sending is a safe no-op until `FCM_SERVER_KEY` is configured. In-app notifications remain the source of truth.

## MVP v1 Checklist

- Authentication and JWT
- Academic structure and student verification
- Groups, files, announcements, and notifications
- Printing orders and print staff workflow
- Group chat REST and WebSocket base
- Support tickets
- Audit logs
- Device tokens for push readiness
- Dashboard stats
- Swagger/OpenAPI validation
- Docker, PostgreSQL, Redis, Channels-ready setup

## Post-MVP Next Steps

- Real WhatsApp OTP integration
- Real FCM provider integration
- Production object storage for media
- Payment/wallet integration
- Print pricing engine
- Read receipts and richer chat moderation
- Advanced analytics
