from django.conf import settings
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import permissions, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from apps.common.responses import error_response, success_response
from apps.common.throttles import (
    LoginRateThrottle,
    OTPRequestRateThrottle,
    OTPVerifyRateThrottle,
    PasswordResetRateThrottle,
    RegistrationRateThrottle,
)
from apps.product.services import feature_enabled_or_raise

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
    throttle_classes = [RegistrationRateThrottle]
    serializer_class = NormalUserRegisterSerializer

    @extend_schema(request=NormalUserRegisterSerializer, responses={201: UserSerializer})
    def post(self, request):
        feature_enabled_or_raise("registrations_enabled", request=request)
        serializer = NormalUserRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user, raw_code, channel = serializer.save()
        data = {"user": UserSerializer(user).data, "otp_channel": channel}
        if settings.RETURN_DEVELOPMENT_OTP and raw_code:
            data["development_otp"] = raw_code
        return success_response(
            data=data,
            message="Normal user registered successfully",
            status_code=status.HTTP_201_CREATED,
            request=request,
            code="USER_REGISTERED",
        )


class StudentRegisterView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [RegistrationRateThrottle]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    serializer_class = StudentRegisterSerializer

    @extend_schema(request=StudentRegisterSerializer, responses={201: UserSerializer})
    def post(self, request):
        feature_enabled_or_raise("registrations_enabled", request=request)
        serializer = StudentRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user, raw_code, channel = serializer.save()
        data = {"user": UserSerializer(user).data, "otp_channel": channel}
        if settings.RETURN_DEVELOPMENT_OTP and raw_code:
            data["development_otp"] = raw_code
        return success_response(
            data=data,
            message="Student registered successfully. Complete the academic profile and submit verification.",
            status_code=status.HTTP_201_CREATED,
            request=request,
            code="STUDENT_REGISTERED",
        )


class LoginView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [LoginRateThrottle]
    serializer_class = LoginSerializer

    @extend_schema(request=LoginSerializer, responses={200: OpenApiResponse(description="JWT login response")})
    def post(self, request):
        feature_enabled_or_raise("otp_email_enabled", request=request)
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        return success_response(
            data={
                "access": serializer.validated_data["access"],
                "refresh": serializer.validated_data["refresh"],
                "user": UserSerializer(user).data,
            },
            message="Logged in successfully",
            request=request,
            code="LOGIN_SUCCEEDED",
        )


class TokenRefreshView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [LoginRateThrottle]
    serializer_class = TokenRefreshSerializer

    @extend_schema(request=TokenRefreshSerializer, responses={200: OpenApiResponse(description="JWT refresh response")})
    def post(self, request):
        feature_enabled_or_raise("otp_email_enabled", request=request)
        serializer = TokenRefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return success_response(
            data=serializer.validated_data,
            message="Token refreshed successfully",
            request=request,
            code="TOKEN_REFRESHED",
        )


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
                request=request,
                code="INVALID_REFRESH_TOKEN",
            )
        return success_response(message="Logged out successfully", request=request, code="LOGOUT_SUCCEEDED")


class CurrentUserView(APIView):
    serializer_class = UserSerializer

    @extend_schema(responses={200: UserSerializer})
    def get(self, request):
        return success_response(data=UserSerializer(request.user).data, request=request)

    @extend_schema(request=UserSerializer, responses={200: UserSerializer})
    def patch(self, request):
        serializer = UserSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success_response(data=serializer.data, message="Profile updated successfully", request=request)


class ChangePasswordView(APIView):
    serializer_class = ChangePasswordSerializer

    @extend_schema(request=ChangePasswordSerializer)
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success_response(
            message="Password changed successfully. Other sessions were revoked.",
            request=request,
            code="PASSWORD_CHANGED",
        )


class SendOTPView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [OTPRequestRateThrottle]
    serializer_class = SendOTPSerializer

    @extend_schema(request=SendOTPSerializer)
    def post(self, request):
        serializer = SendOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        otp, raw_code = serializer.save()
        data = {"expires_at": otp.expires_at, "channel": otp.delivery_channel} if otp else {}
        if settings.RETURN_DEVELOPMENT_OTP and raw_code:
            data["development_otp"] = raw_code
        return success_response(
            data=data,
            message="If the account requires verification, an OTP has been sent.",
            request=request,
            code="OTP_SENT",
        )


class VerifyOTPView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [OTPVerifyRateThrottle]
    serializer_class = VerifyOTPSerializer

    @extend_schema(request=VerifyOTPSerializer)
    def post(self, request):
        serializer = VerifyOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success_response(message="OTP verified successfully", request=request, code="OTP_VERIFIED")


class RequestPasswordResetView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [PasswordResetRateThrottle]
    serializer_class = RequestPasswordResetSerializer

    @extend_schema(request=RequestPasswordResetSerializer)
    def post(self, request):
        serializer = RequestPasswordResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        otp, raw_code = serializer.save()
        data = {}
        if otp:
            data = {"expires_at": otp.expires_at, "channel": otp.delivery_channel}
        if settings.RETURN_DEVELOPMENT_OTP and raw_code:
            data["development_otp"] = raw_code
        return success_response(
            data=data,
            message="If the account exists, a password reset code has been sent.",
            request=request,
            code="PASSWORD_RESET_REQUEST_ACCEPTED",
        )


class ConfirmPasswordResetView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [OTPVerifyRateThrottle]
    serializer_class = ConfirmPasswordResetSerializer

    @extend_schema(request=ConfirmPasswordResetSerializer)
    def post(self, request):
        serializer = ConfirmPasswordResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success_response(
            message="Password reset successfully. Previous sessions were revoked.",
            request=request,
            code="PASSWORD_RESET_SUCCEEDED",
        )
