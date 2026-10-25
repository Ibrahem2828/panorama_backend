from __future__ import annotations

import mimetypes
from pathlib import Path

from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils.http import content_disposition_header
from drf_spectacular.utils import OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework import filters, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.views import APIView

from apps.accounts.permissions import CanReviewEnrollment, IsStudent
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.idempotency import run_idempotent
from apps.common.responses import success_response
from apps.common.viewsets import StandardReadOnlyModelViewSet

from .models import EnrollmentRequest, EnrollmentScreenshotTicket, EnrollmentStatus
from .serializers import (
    BulkDecisionSerializer,
    DashboardEnrollmentSerializer,
    DecisionSerializer,
    EnrollmentCreateSerializer,
    EnrollmentRequestSerializer,
    SubjectBriefSerializer,
)
from .services import EnrollmentConflict, EnrollmentService, search_subjects


class SubjectSearchView(APIView):
    """Type-ahead: ``?q=`` of at least two characters, at most eight results, never the full list."""

    permission_classes = [IsStudent]

    @extend_schema(tags=["Enrollment"], responses={200: SubjectBriefSerializer(many=True)})
    def get(self, request):
        subjects = search_subjects(request.user, request.query_params.get("q", ""))
        return success_response(
            data=SubjectBriefSerializer(subjects, many=True).data, message="Subjects", request=request
        )


class MyEnrollmentsView(APIView):
    permission_classes = [IsStudent]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(tags=["Enrollment"], responses={200: EnrollmentRequestSerializer(many=True)})
    def get(self, request):
        queryset = (
            EnrollmentRequest.objects.filter(user=request.user, is_deleted=False)
            .prefetch_related("items__subject__academic_year", "items__subject__semester")
            .order_by("-created_at", "-id")[:20]
        )
        return success_response(
            data=EnrollmentRequestSerializer(queryset, many=True).data, message="Enrollment requests", request=request
        )

    @extend_schema(
        tags=["Enrollment"], request=EnrollmentCreateSerializer, responses={201: EnrollmentRequestSerializer}
    )
    def post(self, request):
        def write():
            serializer = EnrollmentCreateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            enrollment = EnrollmentService.submit(
                user=request.user,
                screenshot=serializer.validated_data["screenshot"],
                subject_ids=serializer.validated_data["subject_ids"],
                request=request,
            )
            return success_response(
                data=EnrollmentRequestSerializer(enrollment).data,
                message="Enrollment request submitted",
                status_code=status.HTTP_201_CREATED,
                request=request,
                code="ENROLLMENT_SUBMITTED",
            )

        return run_idempotent(request, "enrollment-submit", write)


class CancelEnrollmentView(APIView):
    permission_classes = [IsStudent]

    @extend_schema(tags=["Enrollment"], request=None, responses={200: EnrollmentRequestSerializer})
    def post(self, request, pk: int):
        enrollment = get_object_or_404(EnrollmentRequest, pk=pk, user=request.user, is_deleted=False)
        enrollment = EnrollmentService.cancel(enrollment, request.user, request=request)
        return success_response(
            data=EnrollmentRequestSerializer(enrollment).data,
            message="Enrollment request cancelled",
            request=request,
            code="ENROLLMENT_CANCELLED",
        )


# -- staff ---------------------------------------------------------------------------------------------------------------


class DashboardEnrollmentViewSet(StandardReadOnlyModelViewSet):
    """``GET /dashboard/enrollment/requests/``: filter by ``status``, ``faculty``; search name, e-mail or student number."""

    permission_classes = [CanReviewEnrollment]
    serializer_class = DashboardEnrollmentSerializer
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ["created_at", "status"]
    ordering = ["-created_at", "-id"]

    def get_queryset(self):
        queryset = (
            EnrollmentRequest.objects.filter(is_deleted=False)
            .select_related("user", "student_profile", "faculty", "academic_year", "semester", "reviewed_by")
            .prefetch_related("items__subject__academic_year", "items__subject__semester")
        )
        params = self.request.query_params
        if params.get("status") in EnrollmentStatus.values:
            queryset = queryset.filter(status=params["status"])
        if params.get("faculty", "").isdigit():
            queryset = queryset.filter(faculty_id=int(params["faculty"]))
        term = " ".join(params.get("search", "").split())
        if term:
            queryset = queryset.filter(
                Q(user__full_name__icontains=term)
                | Q(user__email__icontains=term)
                | Q(student_profile__student_number__icontains=term)
            )
        return queryset


class EnrollmentSummaryView(APIView):
    permission_classes = [CanReviewEnrollment]

    @extend_schema(tags=["Dashboard"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        pending = EnrollmentRequest.objects.filter(status=EnrollmentStatus.PENDING, is_deleted=False).count()
        return success_response(data={"pending": pending}, message="Enrollment summary", request=request)


class EnrollmentDecisionView(APIView):
    permission_classes = [CanReviewEnrollment]

    @extend_schema(tags=["Dashboard"], request=DecisionSerializer, responses={200: DashboardEnrollmentSerializer})
    def post(self, request, pk: int):
        serializer = DecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        enrollment = get_object_or_404(EnrollmentRequest, pk=pk, is_deleted=False)
        approve: set[int] | None = {
            "approve_all": None,
            "reject_all": set(),
            "approve_selected": set(data.get("item_ids", [])),
        }[data["action"]]
        enrollment = EnrollmentService.decide(
            enrollment, request.user, approve_item_ids=approve, reason=data.get("reason", ""), request=request
        )
        return success_response(
            data=DashboardEnrollmentSerializer(enrollment).data,
            message="Enrollment decision saved",
            request=request,
            code="ENROLLMENT_REVIEWED",
        )


class EnrollmentBulkDecisionView(APIView):
    """Approve all / reject all across many requests; each request is decided on its own (one failing never blocks the rest)."""

    permission_classes = [CanReviewEnrollment]

    @extend_schema(tags=["Dashboard"], request=BulkDecisionSerializer, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = BulkDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        approve: set[int] | None = None if data["action"] == "approve_all" else set()
        done: list[int] = []
        skipped: list[dict] = []
        for enrollment in EnrollmentRequest.objects.filter(pk__in=data["ids"], is_deleted=False):
            try:
                with transaction.atomic():
                    EnrollmentService.decide(
                        enrollment,
                        request.user,
                        approve_item_ids=approve,
                        reason=data.get("reason", ""),
                        request=request,
                    )
                done.append(enrollment.pk)
            except (EnrollmentConflict, Exception) as exc:  # noqa: BLE001 - reported per request below
                skipped.append({"id": enrollment.pk, "error": getattr(exc, "detail", str(exc))})
        return success_response(
            data={"decided": done, "skipped": skipped}, message="Bulk decision saved", request=request
        )


class EnrollmentScreenshotTicketView(APIView):
    permission_classes = [CanReviewEnrollment]

    @extend_schema(tags=["Dashboard"], request=None, responses={201: OpenApiTypes.OBJECT})
    def post(self, request, pk: int):
        enrollment = get_object_or_404(EnrollmentRequest, pk=pk, is_deleted=False)
        ticket = EnrollmentScreenshotTicket.issue(enrollment, request.user)
        AuditLogService.log(
            actor=request.user,
            action=AuditAction.ENROLLMENT_SCREENSHOT_ACCESSED,
            target=enrollment,
            new_value={"ticket_id": ticket.pk},
            request=request,
        )
        return success_response(
            data={
                "preview_url": request.build_absolute_uri(f"/api/v1/enrollment-screenshot-access/{ticket.token}/"),
                "expires_at": ticket.expires_at,
            },
            message="Protected screenshot ticket issued",
            status_code=status.HTTP_201_CREATED,
            request=request,
            code="ENROLLMENT_SCREENSHOT_TICKET_ISSUED",
        )


class EnrollmentScreenshotStreamView(APIView):
    permission_classes = [CanReviewEnrollment]

    @extend_schema(tags=["Protected Assets"], responses={200: OpenApiResponse(description="Inline protected image")})
    def get(self, request, token):
        with transaction.atomic():
            ticket = (
                EnrollmentScreenshotTicket.objects.select_for_update()
                .select_related("enrollment")
                .filter(token=token, is_deleted=False)
                .first()
            )
            if not ticket or not ticket.is_valid or ticket.requested_by_id != request.user.id:
                raise Http404("The protected access link is invalid or expired.")
            enrollment = ticket.enrollment
            if not enrollment.screenshot:
                raise Http404("The screenshot is unavailable.")
            ticket.use_count += 1
            ticket.save(update_fields=["use_count", "updated_at"])
        filename = Path(enrollment.screenshot.name).name
        response = FileResponse(
            enrollment.screenshot.open("rb"),
            content_type=mimetypes.guess_type(filename)[0] or "application/octet-stream",
        )
        disposition = content_disposition_header(False, filename)
        if disposition:
            response["Content-Disposition"] = disposition
        response["Cache-Control"] = "private, no-store, max-age=0"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = "default-src 'none'; img-src 'self' data:; sandbox"
        return response
