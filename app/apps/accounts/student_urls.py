from django.urls import path

from .student_views import CurrentStudentProfileView, StudentNumberParseView, StudentTermConfirmationView

urlpatterns = [
    path("me/profile/", CurrentStudentProfileView.as_view(), name="student-profile"),
    path("me/term-confirmation/", StudentTermConfirmationView.as_view(), name="student-term-confirmation"),
    path("student-number/parse/", StudentNumberParseView.as_view(), name="student-number-parse"),
]
