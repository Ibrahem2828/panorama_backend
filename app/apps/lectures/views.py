from __future__ import annotations

from pathlib import Path

from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.http import content_disposition_header
from drf_spectacular.utils import OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.views import APIView

from apps.accounts.permissions import CanManageLectures
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.responses import success_response
from apps.common.throttles import LectureNotesRateThrottle, LectureViewerRateThrottle
from apps.common.viewsets import StandardModelViewSet, StandardReadOnlyModelViewSet

from .models import Lecture, LectureNote, LecturePage, LectureViewEvent
from .serializers import DashboardLectureSerializer, LectureNoteSerializer, LectureSerializer
from .services import (
    accessible_lectures_for_user,
    get_authorized_page,
    issue_viewer_session,
    user_can_manage_lectures,
)
from .tasks import process_lecture_document


class NoteVersionConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The note was changed by another session. Refresh and retry."
    default_code = "note_version_conflict"


def _viewer_token(request) -> str:
    token = request.headers.get("X-Viewer-Session", "").strip()
    if not token:
        raise Http404("Viewer session is required.")
    return token


def _inline_file_response(field_file, filename: str, content_type: str) -> FileResponse:
    response = FileResponse(field_file.open("rb"), content_type=content_type)
    disposition = content_disposition_header(False, filename)
    if disposition:
        response["Content-Disposition"] = disposition
    response["Cache-Control"] = "private, no-store, max-age=0"
    response["Pragma"] = "no-cache"
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Frame-Options"] = "DENY"
    return response


class LectureViewSet(StandardReadOnlyModelViewSet):
    serializer_class = LectureSerializer
    ordering = ["-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Lecture.objects.none()
        return accessible_lectures_for_user(self.request.user)


class DashboardLectureViewSet(StandardModelViewSet):
    permission_classes = [CanManageLectures]
    serializer_class = DashboardLectureSerializer
    ordering = ["-created_at"]

    def get_queryset(self):
        return Lecture.objects.filter(is_deleted=False).select_related("subject", "uploaded_by")

    def perform_create(self, serializer):
        lecture = serializer.save(uploaded_by=self.request.user)
        AuditLogService.log(
            actor=self.request.user,
            action=AuditAction.LECTURE_UPLOADED,
            target=lecture,
            new_value={"subject_id": lecture.subject_id, "sha256": lecture.original_sha256},
            request=self.request,
        )
        transaction.on_commit(lambda: process_lecture_document.delay(lecture.id))


class LectureViewerManifestView(APIView):
    throttle_classes = [LectureViewerRateThrottle]

    @extend_schema(tags=["Lecture viewer"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request, pk: int):
        lecture = get_object_or_404(accessible_lectures_for_user(request.user), pk=pk)
        return success_response(
            data={
                "lecture_id": lecture.id,
                "title": lecture.title,
                "status": lecture.status,
                "page_count": lecture.page_count,
                "text_available": LecturePage.objects.filter(lecture=lecture, text_content__gt="").exists(),
                "viewer_capabilities": {"page_streaming": True, "download_original": False, "notes": True},
            },
            request=request,
            code="LECTURE_VIEWER_MANIFEST",
        )


class LectureViewerSessionView(APIView):
    throttle_classes = [LectureViewerRateThrottle]

    @extend_schema(tags=["Lecture viewer"], request=None, responses={201: OpenApiTypes.OBJECT})
    def post(self, request, pk: int):
        lecture = get_object_or_404(accessible_lectures_for_user(request.user), pk=pk)
        session = issue_viewer_session(request.user, lecture)
        AuditLogService.log(
            actor=request.user,
            action=AuditAction.LECTURE_VIEWED,
            target=lecture,
            new_value={"viewer_session_id": session.id},
            request=request,
        )
        return success_response(
            data={"session_token": str(session.token), "expires_at": session.expires_at},
            status_code=status.HTTP_201_CREATED,
            request=request,
            code="LECTURE_VIEWER_SESSION_CREATED",
        )


class LectureViewerPageView(APIView):
    throttle_classes = [LectureViewerRateThrottle]

    @extend_schema(tags=["Lecture viewer"], responses={200: OpenApiResponse(description="Protected page image")})
    def get(self, request, pk: int, page_number: int):
        lecture = get_object_or_404(accessible_lectures_for_user(request.user), pk=pk)
        page = get_authorized_page(request.user, lecture, _viewer_token(request), page_number)
        LectureViewEvent.objects.create(lecture=lecture, user=request.user, page_number=page_number)
        return _inline_file_response(page.rendered_file, f"lecture-{lecture.id}-page-{page_number}.png", "image/png")


class LectureViewerThumbnailView(APIView):
    throttle_classes = [LectureViewerRateThrottle]

    @extend_schema(tags=["Lecture viewer"], responses={200: OpenApiResponse(description="Protected thumbnail image")})
    def get(self, request, pk: int, page_number: int):
        lecture = get_object_or_404(accessible_lectures_for_user(request.user), pk=pk)
        page = get_authorized_page(request.user, lecture, _viewer_token(request), page_number)
        if not page.thumbnail:
            raise Http404("Lecture thumbnail is unavailable.")
        return _inline_file_response(page.thumbnail, f"lecture-{lecture.id}-thumbnail-{page_number}.png", "image/png")


class LectureViewerTextView(APIView):
    throttle_classes = [LectureViewerRateThrottle]

    @extend_schema(tags=["Lecture viewer"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request, pk: int, page_number: int):
        lecture = get_object_or_404(accessible_lectures_for_user(request.user), pk=pk)
        page = get_authorized_page(request.user, lecture, _viewer_token(request), page_number)
        return success_response(
            data={"lecture_id": lecture.id, "page_number": page_number, "text": page.text_content},
            request=request,
            code="LECTURE_PAGE_TEXT",
        )


class LectureProcessingStatusView(APIView):
    @extend_schema(tags=["Lecture viewer"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request, pk: int):
        lecture = get_object_or_404(accessible_lectures_for_user(request.user), pk=pk)
        data = {"status": lecture.status, "page_count": lecture.page_count, "is_ready": lecture.is_ready_for_students}
        if user_can_manage_lectures(request.user):
            data.update({"failure_code": lecture.failure_code, "failure_message": lecture.failure_message})
        return success_response(data=data, request=request, code="LECTURE_PROCESSING_STATUS")


class DashboardLectureOriginalView(APIView):
    permission_classes = [CanManageLectures]

    @extend_schema(
        tags=["Dashboard lectures"], responses={200: OpenApiResponse(description="Original private document")}
    )
    def get(self, request, pk: int):
        lecture = get_object_or_404(Lecture, pk=pk, is_deleted=False)
        if not lecture.original_file:
            raise Http404("Original document is unavailable.")
        filename = Path(lecture.original_filename).name
        return _inline_file_response(lecture.original_file, filename, lecture.original_mime_type)


class LectureNotesView(APIView):
    throttle_classes = [LectureNotesRateThrottle]

    def _lecture(self, request, pk: int) -> Lecture:
        return get_object_or_404(accessible_lectures_for_user(request.user), pk=pk)

    @extend_schema(tags=["Lecture notes"], responses={200: LectureNoteSerializer(many=True)})
    def get(self, request, pk: int):
        lecture = self._lecture(request, pk)
        notes = LectureNote.objects.filter(
            lecture=lecture,
            student=request.user,
            is_deleted=False,
            archived_at__isnull=True,
        ).order_by("-updated_at")
        page_number = request.query_params.get("page_number")
        if page_number:
            try:
                notes = notes.filter(page_number=int(page_number))
            except ValueError as exc:
                raise ValidationError({"page_number": "A positive integer is required."}) from exc
        return success_response(
            data=LectureNoteSerializer(notes, many=True).data, request=request, code="LECTURE_NOTES"
        )

    @extend_schema(tags=["Lecture notes"], request=LectureNoteSerializer, responses={201: LectureNoteSerializer})
    def post(self, request, pk: int):
        lecture = self._lecture(request, pk)
        key = request.headers.get("Idempotency-Key", "").strip()
        key_hash = LectureNoteSerializer.idempotency_hash(key) if key else ""
        if key_hash:
            existing = LectureNote.objects.filter(
                lecture=lecture,
                student=request.user,
                idempotency_key_hash=key_hash,
                is_deleted=False,
            ).first()
            if existing:
                return success_response(
                    data=LectureNoteSerializer(existing).data,
                    request=request,
                    code="LECTURE_NOTE_IDEMPOTENT_REPLAY",
                )
        serializer = LectureNoteSerializer(data=request.data, context={"lecture": lecture})
        serializer.is_valid(raise_exception=True)
        note = serializer.save(student=request.user, lecture=lecture, idempotency_key_hash=key_hash)
        AuditLogService.log(
            actor=request.user,
            action=AuditAction.LECTURE_NOTE_UPDATED,
            target=note,
            new_value={"lecture_id": lecture.id, "note_id": note.id},
            request=request,
        )
        return success_response(
            data=LectureNoteSerializer(note).data,
            status_code=status.HTTP_201_CREATED,
            request=request,
            code="LECTURE_NOTE_CREATED",
        )


class LectureNoteDetailView(APIView):
    throttle_classes = [LectureNotesRateThrottle]

    def _note(self, request, lecture_id: int, note_id: int, *, lock: bool = False) -> tuple[Lecture, LectureNote]:
        lecture = get_object_or_404(accessible_lectures_for_user(request.user), pk=lecture_id)
        queryset = LectureNote.objects.filter(
            lecture=lecture,
            pk=note_id,
            student=request.user,
            is_deleted=False,
            archived_at__isnull=True,
        )
        if lock:
            queryset = queryset.select_for_update()
        note = get_object_or_404(queryset)
        return lecture, note

    @extend_schema(tags=["Lecture notes"], responses={200: LectureNoteSerializer})
    def get(self, request, lecture_id: int, note_id: int):
        _, note = self._note(request, lecture_id, note_id)
        return success_response(data=LectureNoteSerializer(note).data, request=request, code="LECTURE_NOTE")

    @extend_schema(tags=["Lecture notes"], request=LectureNoteSerializer, responses={200: LectureNoteSerializer})
    def patch(self, request, lecture_id: int, note_id: int):
        with transaction.atomic():
            lecture, note = self._note(request, lecture_id, note_id, lock=True)
            try:
                expected_version = int(request.data.get("version"))
            except (TypeError, ValueError) as exc:
                raise ValidationError({"version": "The current note version is required."}) from exc
            if expected_version != note.version:
                raise NoteVersionConflict()
            serializer = LectureNoteSerializer(
                note,
                data=request.data,
                partial=True,
                context={"lecture": lecture},
            )
            serializer.is_valid(raise_exception=True)
            serializer.save(version=note.version + 1)
        return success_response(data=LectureNoteSerializer(note).data, request=request, code="LECTURE_NOTE_UPDATED")

    @extend_schema(tags=["Lecture notes"], responses={200: OpenApiTypes.OBJECT})
    def delete(self, request, lecture_id: int, note_id: int):
        with transaction.atomic():
            _, note = self._note(request, lecture_id, note_id, lock=True)
            note.archived_at = timezone.now()
            note.save(update_fields=["archived_at", "updated_at"])
        return success_response(message="Lecture note archived.", request=request, code="LECTURE_NOTE_ARCHIVED")
