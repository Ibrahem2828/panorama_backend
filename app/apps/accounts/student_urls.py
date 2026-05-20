from django.urls import path

from .student_views import CurrentStudentProfileView, StudentNumberParseView

urlpatterns = [
    path("me/profile/", CurrentStudentProfileView.as_view(), name="student-profile"),
    path("student-number/parse/", StudentNumberParseView.as_view(), name="student-number-parse"),
]
