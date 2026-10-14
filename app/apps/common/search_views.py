"""Unified search: one request instead of the client stitching four calls together.

Visibility is never decided here. Every type is searched inside the queryset the matching per-resource endpoint
already serves to the same user, so a search can only ever return what the user could already list:

* subjects      -> active subjects of the student's own major (what ``/majors/{id}/subjects/`` returns for it)
* lectures      -> ``accessible_lectures_for_user`` (``/lectures/``)
* groups        -> ``visible_groups_for_student`` (``/groups/``)
* files         -> ``accessible_files_for_user`` (``/files/``)
* announcements -> ``announcements_for_user`` (``/announcements/``)

Users who cannot see a type (for example unverified students and groups/lectures) simply get an empty list for it.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Q, QuerySet
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.views import APIView

from apps.accounts.choices import UserRole
from apps.announcements.services import announcements_for_user
from apps.common.responses import success_response
from apps.common.throttles import SearchRateThrottle
from apps.files.services import accessible_files_for_user
from apps.groups.models import Group
from apps.groups.services import visible_groups_for_student
from apps.lectures.services import accessible_lectures_for_user
from apps.universities.models import Subject

MIN_QUERY_LENGTH = 2
MAX_QUERY_LENGTH = 50
DEFAULT_LIMIT = 5
MAX_LIMIT = 20
EXCERPT_LENGTH = 140
SEARCH_TYPES = ("subjects", "lectures", "groups", "files", "announcements")


def _excerpt(value: str | None) -> str:
    text = " ".join((value or "").split())
    return text if len(text) <= EXCERPT_LENGTH else text[: EXCERPT_LENGTH - 1] + "…"


def _match(query: str, *fields: str) -> Q:
    condition = Q()
    for field in fields:
        condition |= Q(**{f"{field}__icontains": query})
    return condition


def _subjects(user) -> QuerySet:
    profile = getattr(user, "student_profile", None)
    if user.role != UserRole.STUDENT or not profile or not profile.major_id:
        return Subject.objects.none()
    return Subject.objects.filter(
        major_id=profile.major_id,
        major__is_active=True,
        academic_year__is_active=True,
        semester__is_active=True,
        is_active=True,
        is_deleted=False,
    ).select_related("academic_year", "semester")


def _serialize_subject(subject: Subject) -> dict[str, Any]:
    return {
        "id": subject.id,
        "type": "subject",
        "name": subject.name,
        "code": subject.code,
        "academic_year": subject.academic_year.name,
        "semester": subject.semester.name,
        "major_id": subject.major_id,
    }


def _serialize_lecture(lecture) -> dict[str, Any]:
    return {
        "id": lecture.id,
        "type": "lecture",
        "title": lecture.title,
        "subject_id": lecture.subject_id,
        "subject_name": lecture.subject.name,
        "page_count": lecture.page_count,
    }


def _serialize_group(group) -> dict[str, Any]:
    return {
        "id": group.id,
        "type": "group",
        "name": group.name,
        "description": _excerpt(group.description),
        "subject_id": group.subject_id,
    }


def _serialize_file(file_resource) -> dict[str, Any]:
    return {
        "id": file_resource.id,
        "type": "file",
        "title": file_resource.title,
        "file_type": file_resource.file_type,
        "subject_id": file_resource.subject_id,
        "subject_name": file_resource.subject.name if file_resource.subject_id else "",
        "group_id": file_resource.group_id,
    }


def _serialize_announcement(announcement) -> dict[str, Any]:
    return {
        "id": announcement.id,
        "type": "announcement",
        "title": announcement.title,
        "description": _excerpt(announcement.description),
        "link": announcement.link,
        "created_at": announcement.created_at,
    }


# type -> (queryset factory, searched fields, ordering, serializer)
_SOURCES = {
    "subjects": (_subjects, ("name", "code", "description"), ("name", "id"), _serialize_subject),
    "lectures": (
        lambda user: accessible_lectures_for_user(user),
        ("title", "description"),
        ("-created_at", "-id"),
        _serialize_lecture,
    ),
    "groups": (
        lambda user: visible_groups_for_student(user) if user.role == UserRole.STUDENT else Group.objects.none(),
        ("name", "description"),
        ("name", "id"),
        _serialize_group,
    ),
    "files": (
        lambda user: accessible_files_for_user(user),
        ("title", "description"),
        ("-created_at", "-id"),
        _serialize_file,
    ),
    "announcements": (
        lambda user: announcements_for_user(user),
        ("title", "description"),
        ("-created_at", "-id"),
        _serialize_announcement,
    ),
}


def _parse_params(params) -> tuple[str, list[str], int]:
    query = (params.get("q") or "").strip()
    if len(query) < MIN_QUERY_LENGTH:
        raise ValidationError({"q": f"Enter at least {MIN_QUERY_LENGTH} characters."})
    if len(query) > MAX_QUERY_LENGTH:
        raise ValidationError({"q": f"Use at most {MAX_QUERY_LENGTH} characters."})

    raw_types = params.get("types")
    if raw_types:
        requested = [item.strip() for item in raw_types.split(",") if item.strip()]
        unknown = sorted(set(requested) - set(SEARCH_TYPES))
        if unknown or not requested:
            raise ValidationError({"types": f"Unknown types: {', '.join(unknown) or '(empty)'}."})
        types = [name for name in SEARCH_TYPES if name in requested]
    else:
        types = list(SEARCH_TYPES)

    raw_limit = params.get("limit")
    if raw_limit in (None, ""):
        limit = DEFAULT_LIMIT
    else:
        try:
            limit = int(raw_limit)
        except ValueError as exc:
            raise ValidationError({"limit": "A positive integer is required."}) from exc
        if limit < 1:
            raise ValidationError({"limit": "A positive integer is required."})
        limit = min(limit, MAX_LIMIT)
    return query, types, limit


class UnifiedSearchView(APIView):
    """Search subjects, lectures, groups, files and announcements the caller may already see."""

    throttle_classes = [SearchRateThrottle]

    @extend_schema(
        tags=["Search"],
        parameters=[
            OpenApiParameter("q", OpenApiTypes.STR, required=True, description="2 to 50 characters, case-insensitive."),
            OpenApiParameter(
                "types",
                OpenApiTypes.STR,
                description="Comma separated subset of subjects,lectures,groups,files,announcements (default all).",
            ),
            OpenApiParameter("limit", OpenApiTypes.INT, description="Results per type, default 5, max 20."),
        ],
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request):
        query, types, limit = _parse_params(request.query_params)
        user = request.user
        data: dict[str, Any] = {name: [] for name in SEARCH_TYPES}
        counts: dict[str, int] = dict.fromkeys(SEARCH_TYPES, 0)
        for name in types:
            factory, fields, ordering, serialize = _SOURCES[name]
            matches = factory(user).filter(_match(query, *fields)).order_by(*ordering)
            counts[name] = matches.count()
            if counts[name]:
                data[name] = [serialize(item) for item in matches[:limit]]
        data["counts"] = counts
        data["query"] = query
        data["limit"] = limit
        return success_response(data=data, request=request, code="SEARCH_RESULTS")
