from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import filters, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.views import APIView

from apps.accounts.permissions import IsAdminOrITSupport, IsStudent
from apps.common.responses import success_response
from apps.common.viewsets import StandardReadOnlyModelViewSet

from .models import VerificationRequest, VerificationStatus
from .serializers import VerificationRequestSerializer, VerificationRequestStudentSerializer, VerificationReviewSerializer
from .services import VerificationService


class SubmitVerificationView(APIView):
    permission_classes = [IsStudent]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    serializer_class = VerificationRequestSerializer
    throttle_scope = "verification_submit"

    @extend_schema(tags=["Verification"], request=VerificationRequestSerializer, responses={201: VerificationRequestStudentSerializer})
    def post(self, request):
        serializer = VerificationRequestSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        verification = serializer.save()
        return success_response(
            data=VerificationRequestStudentSerializer(verification).data,
            message="Verification request submitted successfully",
            status_code=status.HTTP_201_CREATED,
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
        data = VerificationRequestStudentSerializer(verification).data if verification else {}
        return success_response(data=data)


class DashboardVerificationViewSet(StandardReadOnlyModelViewSet):
    permission_classes = [IsAdminOrITSupport]
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
    permission_classes = [IsAdminOrITSupport]
    serializer_class = VerificationReviewSerializer
    target_status = VerificationStatus.APPROVED

    @extend_schema(tags=["Dashboard"], request=VerificationReviewSerializer, responses={200: VerificationRequestSerializer})
    def post(self, request, pk: int):
        serializer = VerificationReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        verification = VerificationRequest.objects.get(pk=pk, is_deleted=False)
        verification = VerificationService.review(
            verification,
            reviewer=request.user,
            status=self.target_status,
            rejection_reason=serializer.validated_data.get("rejection_reason", ""),
            admin_note=serializer.validated_data.get("admin_note", ""),
            request=request,
        )
        return success_response(data=VerificationRequestSerializer(verification).data, message="Verification reviewed successfully")


class ApproveVerificationView(VerificationReviewView):
    target_status = VerificationStatus.APPROVED


class RejectVerificationView(VerificationReviewView):
    target_status = VerificationStatus.REJECTED


class NeedsUpdateVerificationView(VerificationReviewView):
    target_status = VerificationStatus.NEEDS_UPDATE


class VerificationCardPreviewTokenView(APIView):
    permission_classes = [IsAdminOrITSupport]
    serializer_class = VerificationRequestSerializer

    @extend_schema(tags=["Dashboard"])
    def post(self, request, pk: int):
        verification = VerificationRequest.objects.get(pk=pk, is_deleted=False)
        data = VerificationService.create_card_preview_token(verification, request.user, request=request)
        return success_response(data=data, message="Verification card preview token created")
