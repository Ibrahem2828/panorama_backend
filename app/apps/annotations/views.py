from __future__ import annotations

from drf_spectacular.utils import OpenApiTypes, extend_schema
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.views import APIView

from apps.common.responses import success_response
from apps.common.throttles import LectureNotesRateThrottle

from .models import AnnotationTarget
from .services import AnnotationService, resolve_target, search_my_notes

TARGETS = {"lectures": AnnotationTarget.LECTURE, "files": AnnotationTarget.FILE}


class SyncSerializer(serializers.Serializer):
    base_revision = serializers.IntegerField(min_value=0, default=0)
    upserts = serializers.ListField(child=serializers.DictField(), default=list, max_length=200)
    deletes = serializers.ListField(child=serializers.CharField(max_length=64), default=list, max_length=200)


def _target(request, kind: str, pk: int):
    target_type = TARGETS.get(kind)
    if target_type is None:
        raise ValidationError({"kind": "Use lectures or files."})
    return resolve_target(request.user, target_type, pk)


class AnnotationListView(APIView):
    """``GET /annotations/<lectures|files>/<id>/?since=<revision>``: the caller's own marks changed after ``since``."""

    throttle_classes = [LectureNotesRateThrottle]

    @extend_schema(tags=["Annotations"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request, kind: str, pk: int):
        target_type, target_id, _pages = _target(request, kind, pk)
        try:
            since = max(int(request.query_params.get("since", 0)), 0)
        except ValueError as exc:
            raise ValidationError({"since": "A revision number is required."}) from exc
        revision, items = AnnotationService.changes_since(request.user, target_type, target_id, since)
        return success_response(data={"revision": revision, "items": items}, message="Annotations", request=request)


class AnnotationSyncView(APIView):
    throttle_classes = [LectureNotesRateThrottle]

    @extend_schema(tags=["Annotations"], request=SyncSerializer, responses={200: OpenApiTypes.OBJECT})
    def post(self, request, kind: str, pk: int):
        target_type, target_id, page_count = _target(request, kind, pk)
        serializer = SyncSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = AnnotationService.sync(
            request.user,
            target_type,
            target_id,
            page_count,
            base_revision=data["base_revision"],
            upserts=data["upserts"],
            deletes=data["deletes"],
        )
        return success_response(
            data={
                "revision": result.revision,
                "applied": result.applied,
                "conflicts": result.conflicts,
                "changes": result.changes,
            },
            message="Annotations synced",
            request=request,
            code="ANNOTATIONS_SYNCED",
        )


class MyNotesView(APIView):
    """``GET /my-notes/?q=``: the caller's text notes and bookmarks across lectures and files (own data only)."""

    throttle_classes = [LectureNotesRateThrottle]

    @extend_schema(tags=["Annotations"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return success_response(
            data=search_my_notes(request.user, request.query_params.get("q", "")), message="My notes", request=request
        )
