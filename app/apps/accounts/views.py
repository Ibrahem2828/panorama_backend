from django.conf import settings
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import exceptions, permissions, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.common.responses import error_response, success_response

from .choices import OTPPurpose, UserRole
from .otp_contract import (
    build_phone_otp_register_payload,
    build_phone_otp_send_payload,
    build_verify_phone_success_payload,
)
from .serializers import (
    ChangePasswordSerializer,
    ConfirmPasswordResetSerializer,
    LoginSerializer,
    LogoutSerializer,
    NormalUserRegisterSerializer,
    RequestPasswordResetSerializer,
    SendOTPSerializer,
    StudentRegisterSerializer,
    UserSerializer,
    VerifyOTPSerializer,
)


class NormalUserRegisterView(APIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = NormalUserRegisterSerializer
    throttle_scope = "normal_register"

    @extend_schema(request=NormalUserRegisterSerializer, responses={201: UserSerializer})
    def post(self, request):
        serializer = NormalUserRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user, raw_code = serializer.save()
        data = {"user": UserSerializer(user).data, **build_phone_otp_register_payload(user)}
        if settings.RETURN_DEVELOPMENT_OTP and raw_code:
            data["development_otp"] = raw_code
        return success_response(
            data=data,
            message="تم إنشاء الحساب بنجاح. أرسلنا رمز تحقق إلى رقم الجوال لتفعيل الحساب.",
            status_code=status.HTTP_201_CREATED,
            request_id=getattr(request, "request_id", None),
        )


class StudentRegisterView(APIView):
    permission_classes = [permissions.AllowAny]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    serializer_class = StudentRegisterSerializer
    throttle_scope = "register"

    @extend_schema(request=StudentRegisterSerializer, responses={201: UserSerializer})
    def post(self, request):
        serializer = StudentRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user, raw_code = serializer.save()
        data = {"user": UserSerializer(user).data}
        if settings.RETURN_DEVELOPMENT_OTP and raw_code:
            data["development_otp"] = raw_code
        return success_response(data=data, message="Student registered successfully", status_code=status.HTTP_201_CREATED)


class LoginView(APIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = LoginSerializer
    throttle_scope = "login"

    @extend_schema(request=LoginSerializer, responses={200: OpenApiResponse(description="JWT login response")})
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except exceptions.ValidationError:
            AuditLogService.log(
                action=AuditAction.USER_LOGIN_FAILED,
                new_value={"reason": "invalid_credentials"},
                request=request,
            )
            raise
        user = serializer.validated_data["user"]
        if user.role in {UserRole.IT_SUPPORT, UserRole.ADMIN, UserRole.PRINT_STAFF}:
            AuditLogService.log(
                actor=user,
                action=AuditAction.USER_LOGIN_SUCCEEDED,
                target=user,
                new_value={"role": user.role},
                request=request,
            )
        return success_response(
            data={
                "access": serializer.validated_data["access"],
                "refresh": serializer.validated_data["refresh"],
                "user": UserSerializer(user).data,
            },
            message="Logged in successfully",
        )


class TokenRefreshView(APIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = TokenRefreshSerializer

    @extend_schema(request=TokenRefreshSerializer, responses={200: OpenApiResponse(description="JWT refresh response")})
    def post(self, request):
        serializer = TokenRefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return success_response(data=serializer.validated_data, message="Token refreshed successfully")


class LogoutView(APIView):
    serializer_class = LogoutSerializer

    @extend_schema(request=LogoutSerializer)
    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        refresh_token = serializer.validated_data["refresh"]
        try:
            RefreshToken(refresh_token).blacklist()
        except TokenError:
            return error_response(
                message="Invalid token",
                errors={"refresh": "Invalid or expired refresh token."},
                request_id=getattr(request, "request_id", None),
            )
        AuditLogService.log(actor=request.user, action=AuditAction.USER_LOGGED_OUT, target=request.user, request=request)
        return success_response(message="Logged out successfully")


class CurrentUserView(APIView):
    serializer_class = UserSerializer

    @extend_schema(responses={200: UserSerializer})
    def get(self, request):
        return success_response(data=UserSerializer(request.user).data)

    @extend_schema(request=UserSerializer, responses={200: UserSerializer})
    def patch(self, request):
        serializer = UserSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success_response(data=serializer.data, message="Profile updated successfully")


class ChangePasswordView(APIView):
    serializer_class = ChangePasswordSerializer
    throttle_scope = "change_password"

    @extend_schema(request=ChangePasswordSerializer)
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success_response(message="Password changed successfully")


class SendOTPView(APIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = SendOTPSerializer
    throttle_scope = "otp_send"

    @extend_schema(request=SendOTPSerializer)
    def post(self, request):
        serializer = SendOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        otp, raw_code = serializer.save()
        data = build_phone_otp_send_payload(
            purpose=otp.purpose,
            phone_number=otp.phone_number,
            expires_at=otp.expires_at,
        )
        if settings.RETURN_DEVELOPMENT_OTP and raw_code:
            data["development_otp"] = raw_code
        message = (
            "تم إرسال رمز التحقق إلى رقم الجوال."
            if otp.purpose == OTPPurpose.VERIFY_PHONE
            else "OTP sent successfully"
        )
        return success_response(data=data, message=message)


class VerifyOTPView(APIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = VerifyOTPSerializer
    throttle_scope = "otp_verify"

    @extend_schema(request=VerifyOTPSerializer)
    def post(self, request):
        serializer = VerifyOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        otp = serializer.save()
        data = build_verify_phone_success_payload() if otp.purpose == OTPPurpose.VERIFY_PHONE else {}
        message = (
            "تم التحقق من رقم الجوال بنجاح. يمكنك تسجيل الدخول الآن."
            if otp.purpose == OTPPurpose.VERIFY_PHONE
            else "OTP verified successfully"
        )
        return success_response(message=message, data=data)


class VerifyPhoneView(APIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = VerifyOTPSerializer
    throttle_scope = "otp_verify"

    @extend_schema(request=VerifyOTPSerializer, tags=["Auth"])
    def post(self, request):
        payload = request.data.copy()
        if hasattr(payload, "dict"):
            payload = payload.dict()
        else:
            payload = dict(payload)
        payload.setdefault("purpose", OTPPurpose.VERIFY_PHONE)
        serializer = VerifyOTPSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success_response(
            data=build_verify_phone_success_payload(),
            message="تم التحقق من رقم الجوال بنجاح. يمكنك تسجيل الدخول الآن.",
        )


class RequestPasswordResetView(APIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = RequestPasswordResetSerializer
    throttle_scope = "password_reset"

    @extend_schema(request=RequestPasswordResetSerializer)
    def post(self, request):
        serializer = RequestPasswordResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        otp, raw_code = serializer.save()
        data = {"expires_at": otp.expires_at} if otp else {}
        if otp and settings.RETURN_DEVELOPMENT_OTP and raw_code:
            data["development_otp"] = raw_code
        return success_response(data=data, message="Password reset OTP sent successfully")


class ConfirmPasswordResetView(APIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = ConfirmPasswordResetSerializer
    throttle_scope = "password_reset"

    @extend_schema(request=ConfirmPasswordResetSerializer)
    def post(self, request):
        serializer = ConfirmPasswordResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success_response(message="Password reset successfully")
