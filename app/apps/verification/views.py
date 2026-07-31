from __future__ import annotations

import mimetypes
from pathlib import Path

from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils.http import content_disposition_header
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework import filters, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.views import APIView

from apps.accounts.permissions import CanReviewVerification, IsStudent
from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.responses import success_response
from apps.common.viewsets import StandardReadOnlyModelViewSet

from .models import VerificationCardAccessTicket, VerificationRequest, VerificationStatus
from .serializers import (
    VerificationRequestSerializer,
    VerificationRequestStudentSerializer,
    VerificationReviewSerializer,
)
from .services import VerificationService


class SubmitVerificationView(APIView):
    permission_classes = [IsStudent]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    serializer_class = VerificationRequestSerializer

    @extend_schema(
        tags=["Verification"],
        request=VerificationRequestSerializer,
        responses={201: VerificationRequestStudentSerializer},
    )
    def post(self, request):
        serializer = VerificationRequestSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        verification = serializer.save()
        AuditLogService.log(
            actor=request.user,
            action=AuditAction.VERIFICATION_SUBMITTED,
            target=verification,
            new_value={"status": verification.status, "student_number": verification.student_number},
            request=request,
        )
        return success_response(
            data=VerificationRequestStudentSerializer(verification, context={"request": request}).data,
            message="Verification request submitted successfully",
            status_code=status.HTTP_201_CREATED,
            request=request,
            code="VERIFICATION_SUBMITTED",
        )


class MyVerificationView(APIView):
    permission_classes = [IsStudent]
    serializer_class = VerificationRequestStudentSerializer

    @extend_schema(tags=["Verification"], responses={200: VerificationRequestStudentSerializer})
    def get(self, request):
        verification = (
            VerificationRequest.objects.filter(user=request.user, is_deleted=False)
            .select_related("university", "faculty", "major", "academic_year", "semester")
            .first()
        )
        data = (
            VerificationRequestStudentSerializer(verification, context={"request": request}).data
            if verification
            else {}
        )
        return success_response(data=data, request=request)


class DashboardVerificationViewSet(StandardReadOnlyModelViewSet):
    permission_classes = [CanReviewVerification]
    serializer_class = VerificationRequestSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["status", "university", "faculty", "major", "academic_year"]
    search_fields = ["user__full_name", "user__email", "user__phone_number", "student_number"]
    ordering_fields = ["created_at", "reviewed_at", "status"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return VerificationRequest.objects.filter(is_deleted=False).select_related(
            "user", "university", "faculty", "major", "academic_year", "semester", "reviewed_by"
        )


class VerificationReviewView(APIView):
    permission_classes = [CanReviewVerification]
    serializer_class = VerificationReviewSerializer
    target_status = VerificationStatus.APPROVED

    @extend_schema(
        tags=["Dashboard"], request=VerificationReviewSerializer, responses={200: VerificationRequestSerializer}
    )
    def post(self, request, pk: int):
        serializer = VerificationReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        verification = get_object_or_404(VerificationRequest, pk=pk, is_deleted=False)
        verification = VerificationService.review(
            verification,
            reviewer=request.user,
            status=self.target_status,
            rejection_reason=serializer.validated_data.get("rejection_reason", ""),
            admin_note=serializer.validated_data.get("admin_note", ""),
            request=request,
        )
        return success_response(
            data=VerificationRequestSerializer(verification, context={"request": request}).data,
            message="Verification reviewed successfully",
            request=request,
            code="VERIFICATION_REVIEWED",
        )


class ApproveVerificationView(VerificationReviewView):
    target_status = VerificationStatus.APPROVED


class RejectVerificationView(VerificationReviewView):
    target_status = VerificationStatus.REJECTED


class NeedsUpdateVerificationView(VerificationReviewView):
    target_status = VerificationStatus.NEEDS_UPDATE


class VerificationCardTicketView(APIView):
    permission_classes = [CanReviewVerification]

    @extend_schema(tags=["Dashboard"], request=None, responses={201: OpenApiTypes.OBJECT})
    def post(self, request, pk: int):
        verification = get_object_or_404(VerificationRequest, pk=pk, is_deleted=False)
        ticket = VerificationCardAccessTicket.issue(verification, request.user)
        url = request.build_absolute_uri(f"/api/v1/verification-card-access/{ticket.token}/")
        AuditLogService.log(
            actor=request.user,
            action=AuditAction.VERIFICATION_CARD_ACCESSED,
            target=verification,
            new_value={"ticket_id": ticket.id, "expires_at": ticket.expires_at.isoformat()},
            request=request,
        )
        return success_response(
            data={"preview_url": url, "expires_at": ticket.expires_at},
            message="Protected card access ticket issued",
            status_code=status.HTTP_201_CREATED,
            request=request,
            code="VERIFICATION_CARD_TICKET_ISSUED",
        )


class VerificationCardStreamView(APIView):
    permission_classes = [CanReviewVerification]

    @extend_schema(
        tags=["Protected Assets"], responses={200: OpenApiResponse(description="Inline protected card image")}
    )
    def get(self, request, token):
        with transaction.atomic():
            ticket = (
                VerificationCardAccessTicket.objects.select_for_update()
                .select_related("verification")
                .filter(token=token, is_deleted=False)
                .first()
            )
            if not ticket or not ticket.is_valid or ticket.requested_by_id != request.user.id:
                raise Http404("The protected access link is invalid or expired.")
            verification = ticket.verification
            if not verification.card_image:
                raise Http404("Verification card is unavailable.")
            ticket.use_count += 1
            ticket.save(update_fields=["use_count", "updated_at"])
        filename = Path(verification.card_image.name).name
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        response = FileResponse(verification.card_image.open("rb"), content_type=content_type)
        content_disposition = content_disposition_header(False, filename)
        if content_disposition:
            response["Content-Disposition"] = content_disposition
        response["Cache-Control"] = "private, no-store, max-age=0"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = "default-src 'none'; img-src 'self' data:; sandbox"
        return response
