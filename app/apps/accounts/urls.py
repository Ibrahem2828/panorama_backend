from django.urls import path

from .staff_views import AcceptInvitationView, InvitationInfoView
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
    path("request-password-reset/", RequestPasswordResetView.as_view(), name="request-password-reset"),
    path("invitations/accept/", AcceptInvitationView.as_view(), name="invitation-accept"),
    path("invitations/<str:token>/", InvitationInfoView.as_view(), name="invitation-info"),
    path("confirm-password-reset/", ConfirmPasswordResetView.as_view(), name="confirm-password-reset"),
]
