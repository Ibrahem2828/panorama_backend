"""Phone verification endpoints: the student's own (``auth/phone-verification/``) and the staff queue."""

from __future__ import annotations

from django.conf import settings
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import filters, serializers, status
from rest_framework.views import APIView

from apps.common.responses import success_response
from apps.common.throttles import PhoneVerificationConfirmThrottle, PhoneVerificationCreateThrottle
from apps.common.viewsets import StandardReadOnlyModelViewSet

from .models import PhoneVerificationRequest
from .permissions import CanReviewVerification, IsStudent
from .phone_verification import PhoneVerificationRefused, PhoneVerificationService, refusal_response


class PhoneVerificationCreateSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=32)
    password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)


class PhoneVerificationConfirmSerializer(serializers.Serializer):
    code = serializers.RegexField(r"^\d{6}$", max_length=6, error_messages={"invalid": "The code is 6 digits."})


class PhoneVerificationRejectSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class MyPhoneVerificationSerializer(serializers.ModelSerializer):
    attempts_remaining = serializers.SerializerMethodField()

    class Meta:
        model = PhoneVerificationRequest
        fields = [
            "id",
            "phone_number",
            "status",
            "created_at",
            "code_sent_at",
            "expires_at",
            "attempts_remaining",
            "rejection_reason",
            "verified_at",
        ]
        read_only_fields = fields

    def get_attempts_remaining(self, obj) -> int:
        return max(0, int(settings.PHONE_VERIFICATION_MAX_ATTEMPTS) - obj.attempts)


class DashboardPhoneVerificationSerializer(serializers.ModelSerializer):
    """What staff see. The code is NOT here: it is returned once by ``claim``."""

    user_id = serializers.IntegerField(read_only=True)
    user_name = serializers.CharField(source="user.full_name", read_only=True)
    claimed_by_name = serializers.CharField(source="claimed_by.full_name", read_only=True, default=None)
    code_generated = serializers.SerializerMethodField()

    class Meta:
        model = PhoneVerificationRequest
        fields = [
            "id",
            "user_id",
            "user_name",
            "phone_number",
            "status",
            "created_at",
            "claimed_by_name",
            "claimed_at",
            "code_generated",
            "code_sent_at",
            "expires_at",
            "attempts",
            "rejection_reason",
            "verified_at",
        ]
        read_only_fields = fields

    def get_code_generated(self, obj) -> bool:
        return bool(obj.code_hash)


class DashboardPhoneVerificationClaimSerializer(DashboardPhoneVerificationSerializer):
    code = serializers.CharField(read_only=True)

    class Meta(DashboardPhoneVerificationSerializer.Meta):
        fields = [*DashboardPhoneVerificationSerializer.Meta.fields, "code"]
        read_only_fields = fields


def _no_store(response):
    response["Cache-Control"] = "no-store, max-age=0"
    response["Pragma"] = "no-cache"
    return response


class PhoneVerificationView(APIView):
    """``POST auth/phone-verification/``: ask to verify a phone number (phone + the account password)."""

    permission_classes = [IsStudent]
    throttle_classes = [PhoneVerificationCreateThrottle]
    serializer_class = PhoneVerificationCreateSerializer

    @extend_schema(
        tags=["Auth"],
        request=PhoneVerificationCreateSerializer,
        responses={201: MyPhoneVerificationSerializer, 409: OpenApiResponse(description="Conflict")},
    )
    def post(self, request):
        serializer = PhoneVerificationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            req = PhoneVerificationService.create(
                request.user, serializer.validated_data["phone_number"], serializer.validated_data["password"], request
            )
        except PhoneVerificationRefused as refusal:
            return refusal_response(refusal, request)
        return success_response(
            data=MyPhoneVerificationSerializer(req).data,
            message="Phone verification requested. A staff member will send you a code on WhatsApp.",
            status_code=status.HTTP_201_CREATED,
            request=request,
            code="PHONE_VERIFICATION_REQUESTED",
        )


class MyPhoneVerificationView(APIView):
    """``GET auth/phone-verification/current/``: the latest request (``data.request`` is null when there never was one)."""

    permission_classes = [IsStudent]
    serializer_class = MyPhoneVerificationSerializer

    @extend_schema(tags=["Auth"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        req = PhoneVerificationService.current(request.user)
        return success_response(
            data={"request": MyPhoneVerificationSerializer(req).data if req else None}, request=request
        )


class PhoneVerificationConfirmView(APIView):
    permission_classes = [IsStudent]
    throttle_classes = [PhoneVerificationConfirmThrottle]
    serializer_class = PhoneVerificationConfirmSerializer

    @extend_schema(
        tags=["Auth"],
        request=PhoneVerificationConfirmSerializer,
        responses={200: MyPhoneVerificationSerializer, 400: OpenApiResponse(description="Wrong or expired code")},
    )
    def post(self, request):
        serializer = PhoneVerificationConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            req = PhoneVerificationService.confirm(request.user, serializer.validated_data["code"], request)
        except PhoneVerificationRefused as refusal:
            return refusal_response(refusal, request)
        return success_response(
            data=MyPhoneVerificationSerializer(req).data,
            message="Phone number verified",
            request=request,
            code="PHONE_VERIFIED",
        )


class PhoneVerificationCancelView(APIView):
    permission_classes = [IsStudent]
    serializer_class = MyPhoneVerificationSerializer

    @extend_schema(tags=["Auth"], request=None, responses={200: MyPhoneVerificationSerializer})
    def post(self, request):
        try:
            req = PhoneVerificationService.cancel(request.user, request)
        except PhoneVerificationRefused as refusal:
            return refusal_response(refusal, request)
        return success_response(
            data=MyPhoneVerificationSerializer(req).data,
            message="Phone verification cancelled",
            request=request,
            code="PHONE_VERIFICATION_CANCELLED",
        )


# ---- dashboard -------------------------------------------------------------------------------------------------------


class DashboardPhoneVerificationViewSet(StandardReadOnlyModelViewSet):
    """``GET dashboard/phone-verifications/``: the staff queue. ``status`` filter, ``search`` on name, e-mail or phone;
    oldest first by default so nobody waits behind newer requests."""

    permission_classes = [CanReviewVerification]
    serializer_class = DashboardPhoneVerificationSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["status"]
    search_fields = ["user__full_name", "user__email", "phone_number"]
    ordering_fields = ["created_at", "claimed_at"]
    ordering = ["created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return PhoneVerificationRequest.objects.none()
        PhoneVerificationService.expire_stale()
        return PhoneVerificationRequest.objects.filter(is_deleted=False).select_related("user", "claimed_by")


class DashboardPhoneVerificationClaimView(APIView):
    """``POST dashboard/phone-verifications/{id}/claim/``: generates the code and returns it ONCE (``data.code``). Send it
    to the student by WhatsApp, then call ``mark-sent``. Calling it again issues a new code."""

    permission_classes = [CanReviewVerification]
    serializer_class = DashboardPhoneVerificationClaimSerializer

    @extend_schema(tags=["Dashboard"], request=None, responses={200: DashboardPhoneVerificationClaimSerializer})
    def post(self, request, pk: int):
        try:
            req, code = PhoneVerificationService.claim(pk, request.user, request)
        except PhoneVerificationRefused as refusal:
            return refusal_response(refusal, request)
        req.code = code  # type: ignore[attr-defined]  # shown once, never stored in clear
        response = success_response(
            data=DashboardPhoneVerificationClaimSerializer(req).data,
            message="Code generated. Send it to the student on WhatsApp, then confirm.",
            request=request,
            code="PHONE_VERIFICATION_CLAIMED",
        )
        return _no_store(response)


class DashboardPhoneVerificationMarkSentView(APIView):
    permission_classes = [CanReviewVerification]
    serializer_class = DashboardPhoneVerificationSerializer

    @extend_schema(tags=["Dashboard"], request=None, responses={200: DashboardPhoneVerificationSerializer})
    def post(self, request, pk: int):
        try:
            req = PhoneVerificationService.mark_sent(pk, request.user, request)
        except PhoneVerificationRefused as refusal:
            return refusal_response(refusal, request)
        return success_response(
            data=DashboardPhoneVerificationSerializer(req).data,
            message="Marked as sent. The code is valid for a limited time.",
            request=request,
            code="PHONE_VERIFICATION_CODE_SENT",
        )


class DashboardPhoneVerificationRejectView(APIView):
    permission_classes = [CanReviewVerification]
    serializer_class = PhoneVerificationRejectSerializer

    @extend_schema(
        tags=["Dashboard"],
        request=PhoneVerificationRejectSerializer,
        responses={200: DashboardPhoneVerificationSerializer},
    )
    def post(self, request, pk: int):
        serializer = PhoneVerificationRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            req = PhoneVerificationService.reject(pk, request.user, serializer.validated_data["reason"], request)
        except PhoneVerificationRefused as refusal:
            return refusal_response(refusal, request)
        return success_response(
            data=DashboardPhoneVerificationSerializer(req).data,
            message="Phone verification rejected",
            request=request,
            code="PHONE_VERIFICATION_REJECTED",
        )
