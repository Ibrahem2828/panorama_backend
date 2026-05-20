# Panorama Dashboard Integration

## Overview

- Base URL: `API_BASE_URL`
- API prefix: `/api/v1/`
- Auth: `Authorization: Bearer <access_token>`
- Collection: `docs/api/dashboard_api_collection.json`

Allowed dashboard roles:

- `it_support`
- `admin`
- `print_staff` for printing dashboard only

Responses use the same `success/message/data` or `success/message/errors` envelope. Lists are paginated with `count`, `next`, `previous`, and `results`. Most list endpoints support `search`, filters, and `ordering`.

## Login Flow

Use `/api/v1/auth/login/`, store access/refresh tokens, call `/api/v1/auth/me/`, and render modules based on `user.role`.

## Layout Recommendation

1. Overview
2. Academic Structure
3. Verification Requests
4. Groups
5. Files
6. Announcements
7. Printing
8. Support
9. Audit Logs
10. Settings

## Academic Management

Create data in this order:

1. University
2. Faculty with parser-compatible code when applicable
3. Major
4. Academic years
5. Semesters
6. Subjects

Use faculty codes `1` to `7` for SPU student-number parsing.

## Verification Management

Use `/api/v1/dashboard/verifications/`.

The detail/list serializer includes parsed student-number data:

- detected faculty code
- detected faculty name
- enrollment year
- serial number

Actions:

- approve: `/approve/`
- reject: `/reject/`
- needs update: `/needs-update/`

## Groups Management

Use `/api/v1/dashboard/groups/`.

Dashboard can:

- create/update image
- set `send_messages_permission`
- manage join requests
- approve/reject/block members
- update membership role at `/api/v1/dashboard/group-memberships/{id}/role/`

Roles: `member`, `moderator`, `group_admin`.

## Files Management

Use `/api/v1/dashboard/files/`.

Set visibility carefully:

- `public`
- `students_only`
- `verified_students_only`
- `major_only`
- `group_only`
- `admin_only`

Group-only files require `group`. Major-only files require `major` and `academic_year`.

## Announcements

Use `/api/v1/dashboard/announcements/`.

Target by:

- user type
- university
- faculty
- major
- year
- semester
- start/end date

Inactive or expired announcements are hidden from mobile users.

## Printing

Use `/api/v1/dashboard/printing/orders/`.

Print staff, Admin, and IT can:

- filter queue by status/priority
- assign order
- update status
- add internal notes

Valid flow: `submitted -> under_review -> accepted -> printing -> ready -> delivered`.

## Support

Use `/api/v1/dashboard/support/tickets/`.

Admin/IT can:

- filter by status/priority/category
- assign ticket
- update status
- update priority
- reply to user
- close or resolve ticket

Admin replies and status changes notify the user.

## Audit Logs

Use `/api/v1/dashboard/audit-logs/`.

Filter by action, actor, target type, target id, or search. Sensitive values are redacted by backend.

## Suggested Build Order

1. Auth
2. Dashboard Stats
3. Academic Structure
4. Verification
5. Groups
6. Files
7. Announcements
8. Printing
9. Support
10. Audit Logs
