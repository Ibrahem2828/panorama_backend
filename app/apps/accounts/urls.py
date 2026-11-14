from django.urls import path

from .phone_views import (
    MyPhoneVerificationView,
    PhoneVerificationCancelView,
    PhoneVerificationConfirmView,
    PhoneVerificationView,
)
from .staff_views import AcceptInvitationView, InvitationInfoView
from .views import (
    ChangePasswordView,
    ConfirmPasswordResetView,
    CurrentUserView,
    DeviceSwitchView,
    LoginView,
    LogoutView,
    NormalUserRegisterView,
    RequestPasswordResetView,
    SendOTPView,
    StudentRegisterView,
    TokenRefreshView,
    VerifyOTPView,
)

urlpatterns = [
    path("register/normal/", NormalUserRegisterView.as_view(), name="register-normal"),
    path("register/student/", StudentRegisterView.as_view(), name="register-student"),
    path("login/", LoginView.as_view(), name="login"),
    path("phone-verification/", PhoneVerificationView.as_view(), name="phone-verification"),
    path("phone-verification/current/", MyPhoneVerificationView.as_view(), name="phone-verification-current"),
    path("phone-verification/confirm/", PhoneVerificationConfirmView.as_view(), name="phone-verification-confirm"),
    path("phone-verification/cancel/", PhoneVerificationCancelView.as_view(), name="phone-verification-cancel"),
    path("device/switch/", DeviceSwitchView.as_view(), name="device-switch"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("me/", CurrentUserView.as_view(), name="current-user"),
    path("change-password/", ChangePasswordView.as_view(), name="change-password"),
    path("otp/send/", SendOTPView.as_view(), name="otp-send"),
    path("otp/verify/", VerifyOTPView.as_view(), name="otp-verify"),
    path("request-password-reset/", RequestPasswordResetView.as_view(), name="request-password-reset"),
    path("invitations/accept/", AcceptInvitationView.as_view(), name="invitation-accept"),
    path("invitations/<str:token>/", InvitationInfoView.as_view(), name="invitation-info"),
    path("confirm-password-reset/", ConfirmPasswordResetView.as_view(), name="confirm-password-reset"),
]
