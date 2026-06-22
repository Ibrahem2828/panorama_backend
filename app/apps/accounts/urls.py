from django.urls import path

from .student_account_request_views import (
    StudentAccountRequestCreateView,
    StudentAccountRequestStatusView,
    StudentAccountRequestVerifyOtpView,
)
from .views import (
    ChangePasswordView,
    ConfirmPasswordResetView,
    CurrentUserView,
    LoginView,
    LogoutView,
    NormalUserRegisterView,
    RequestPasswordResetView,
    SendOTPView,
    StudentRegisterView,
    TokenRefreshView,
    VerifyOTPView,
    VerifyPhoneView,
)

urlpatterns = [
    path("register/normal/", NormalUserRegisterView.as_view(), name="register-normal"),
    path("register/student/", StudentRegisterView.as_view(), name="register-student"),
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("me/", CurrentUserView.as_view(), name="current-user"),
    path("change-password/", ChangePasswordView.as_view(), name="change-password"),
    path("otp/send/", SendOTPView.as_view(), name="otp-send"),
    path("otp/verify/", VerifyOTPView.as_view(), name="otp-verify"),
    path("verify-phone/", VerifyPhoneView.as_view(), name="verify-phone"),
    path("student-account-requests/", StudentAccountRequestCreateView.as_view(), name="student-account-request-create"),
    path(
        "student-account-requests/<uuid:public_id>/status/",
        StudentAccountRequestStatusView.as_view(),
        name="student-account-request-status",
    ),
    path(
        "student-account-requests/<uuid:public_id>/verify-otp/",
        StudentAccountRequestVerifyOtpView.as_view(),
        name="student-account-request-verify-otp",
    ),
    path("request-password-reset/", RequestPasswordResetView.as_view(), name="request-password-reset"),
    path("confirm-password-reset/", ConfirmPasswordResetView.as_view(), name="confirm-password-reset"),
]
