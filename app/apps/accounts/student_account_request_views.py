from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import filters, permissions, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.views import APIView

from apps.accounts.permissions import IsAdminOrITSupport
from apps.accounts.student_account_request_models import StudentAccountRequest
from apps.accounts.student_account_request_serializers import (
    StudentAccountRequestAdminDetailSerializer,
    StudentAccountRequestAdminListSerializer,
    StudentAccountRequestApproveSerializer,
    StudentAccountRequestCreateSerializer,
    StudentAccountRequestNeedsUpdateSerializer,
    StudentAccountRequestRejectSerializer,
    StudentAccountRequestResendOtpSerializer,
    StudentAccountRequestStatusSerializer,
    StudentAccountRequestVerifyOtpSerializer,
)
from apps.accounts.student_account_request_service import StudentAccountRequestService
from apps.common.responses import success_response
from apps.common.viewsets import StandardReadOnlyModelViewSet


class StudentAccountRequestCreateView(APIView):
    permission_classes = [permissions.AllowAny]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    serializer_class = StudentAccountRequestCreateSerializer
    throttle_scope = "student_account_request"

    @extend_schema(tags=["Auth"], request=StudentAccountRequestCreateSerializer)
    def post(self, request):
        serializer = StudentAccountRequestCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        request_obj = serializer.save()
        return success_response(
            data={
                "request_id": str(request_obj.public_id),
                "status": request_obj.status,
                "next_step": "admin_review",
            },
            message="تم إرسال طلب إنشاء حساب الطالب بنجاح. سيتم مراجعة بياناتك من قبل الإدارة.",
            status_code=status.HTTP_201_CREATED,
        )


class StudentAccountRequestStatusView(APIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = StudentAccountRequestStatusSerializer
    throttle_scope = "student_account_request_status"

    @extend_schema(tags=["Auth"], responses={200: StudentAccountRequestStatusSerializer})
    def get(self, request, public_id):
        phone_number = request.query_params.get("phone_number", "").strip()
        request_obj = StudentAccountRequestService.get_request_or_404(public_id)
        if phone_number and phone_number != request_obj.phone_number:
            from rest_framework.exceptions import ValidationError

            raise ValidationError({"phone_number": "Phone number does not match this request."})
        return success_response(data=StudentAccountRequestStatusSerializer(request_obj).data)


class StudentAccountRequestVerifyOtpView(APIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = StudentAccountRequestVerifyOtpSerializer
    throttle_scope = "student_account_request_otp_verify"

    @extend_schema(tags=["Auth"], request=StudentAccountRequestVerifyOtpSerializer)
    def post(self, request, public_id):
        serializer = StudentAccountRequestVerifyOtpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        request_obj = StudentAccountRequestService.get_request_or_404(public_id)
        request_obj = StudentAccountRequestService.verify_otp(
            request_obj,
            serializer.validated_data["code"],
            request=request,
        )
        return success_response(
            data={"status": request_obj.status, "next_step": "login"},
            message="تم تفعيل حساب الطالب بنجاح. يمكنك تسجيل الدخول الآن.",
        )


class DashboardStudentAccountRequestViewSet(StandardReadOnlyModelViewSet):
    permission_classes = [IsAdminOrITSupport]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["status", "university", "faculty", "major"]
    search_fields = ["full_name", "email", "phone_number", "student_number"]
    ordering_fields = ["created_at", "reviewed_at", "status"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return StudentAccountRequest.objects.filter(is_deleted=False).select_related(
            "university",
            "faculty",
            "major",
            "reviewed_by",
            "created_user",
        )

    def get_serializer_class(self):
        if self.action == "retrieve":
            return StudentAccountRequestAdminDetailSerializer
        return StudentAccountRequestAdminListSerializer


class StudentAccountRequestApproveView(APIView):
    permission_classes = [IsAdminOrITSupport]
    serializer_class = StudentAccountRequestApproveSerializer

    @extend_schema(tags=["Dashboard"], request=StudentAccountRequestApproveSerializer)
    def post(self, request, pk: int):
        serializer = StudentAccountRequestApproveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        request_obj = StudentAccountRequestService.get_dashboard_request_or_404(pk)
        request_obj, raw_otp = StudentAccountRequestService.approve(
            request_obj,
            reviewer=request.user,
            admin_note=serializer.validated_data.get("admin_note", ""),
            request=request,
        )
        data = StudentAccountRequestService.build_dashboard_otp_payload(request_obj, raw_otp)
        data["detail"] = StudentAccountRequestAdminDetailSerializer(request_obj).data
        return success_response(
            data=data,
            message="تم قبول الطلب وتوليد رمز التفعيل.",
        )


class StudentAccountRequestRejectView(APIView):
    permission_classes = [IsAdminOrITSupport]
    serializer_class = StudentAccountRequestRejectSerializer

    @extend_schema(tags=["Dashboard"], request=StudentAccountRequestRejectSerializer)
    def post(self, request, pk: int):
        serializer = StudentAccountRequestRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        request_obj = StudentAccountRequestService.get_dashboard_request_or_404(pk)
        request_obj = StudentAccountRequestService.reject(
            request_obj,
            reviewer=request.user,
            rejection_reason=serializer.validated_data["rejection_reason"],
            admin_note=serializer.validated_data.get("admin_note", ""),
            request=request,
        )
        return success_response(
            data=StudentAccountRequestAdminDetailSerializer(request_obj).data,
            message="تم رفض طلب إنشاء الحساب.",
        )


class StudentAccountRequestNeedsUpdateView(APIView):
    permission_classes = [IsAdminOrITSupport]
    serializer_class = StudentAccountRequestNeedsUpdateSerializer

    @extend_schema(tags=["Dashboard"], request=StudentAccountRequestNeedsUpdateSerializer)
    def post(self, request, pk: int):
        serializer = StudentAccountRequestNeedsUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        request_obj = StudentAccountRequestService.get_dashboard_request_or_404(pk)
        request_obj = StudentAccountRequestService.needs_update(
            request_obj,
            reviewer=request.user,
            needs_update_reason=serializer.validated_data["needs_update_reason"],
            admin_note=serializer.validated_data.get("admin_note", ""),
            request=request,
        )
        return success_response(
            data=StudentAccountRequestAdminDetailSerializer(request_obj).data,
            message="تم تحديد أن الطلب يحتاج إلى تحديث.",
        )


class StudentAccountRequestResendOtpView(APIView):
    permission_classes = [IsAdminOrITSupport]
    serializer_class = StudentAccountRequestResendOtpSerializer

    @extend_schema(tags=["Dashboard"], request=StudentAccountRequestResendOtpSerializer)
    def post(self, request, pk: int):
        serializer = StudentAccountRequestResendOtpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        request_obj = StudentAccountRequestService.get_dashboard_request_or_404(pk)
        request_obj, raw_otp = StudentAccountRequestService.resend_otp(
            request_obj,
            reviewer=request.user,
            request=request,
        )
        return success_response(
            data=StudentAccountRequestService.build_dashboard_otp_payload(request_obj, raw_otp),
            message="تم إعادة إرسال رمز التفعيل.",
        )


class StudentAccountRequestCardPreviewTokenView(APIView):
    permission_classes = [IsAdminOrITSupport]
    serializer_class = StudentAccountRequestAdminDetailSerializer

    @extend_schema(tags=["Dashboard"])
    def post(self, request, pk: int):
        request_obj = StudentAccountRequestService.get_dashboard_request_or_404(pk)
        data = StudentAccountRequestService.create_card_preview_token(request_obj, request.user, request=request)
        return success_response(data=data, message="Student account card preview token created")