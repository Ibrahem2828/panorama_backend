from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.views import APIView

from apps.accounts.choices import UserRole
from apps.accounts.serializers import StudentAcademicProfileSerializer
from apps.accounts.student_number import StudentNumberParser
from apps.common.responses import success_response


class CurrentStudentProfileView(APIView):
    serializer_class = StudentAcademicProfileSerializer

    def _get_profile(self, request):
        if request.user.role != UserRole.STUDENT:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("Only students can access this endpoint.")
        return request.user.student_profile

    @extend_schema(tags=["Students"], responses={200: StudentAcademicProfileSerializer})
    def get(self, request):
        return success_response(data=StudentAcademicProfileSerializer(self._get_profile(request)).data)

    @extend_schema(
        tags=["Students"], request=StudentAcademicProfileSerializer, responses={200: StudentAcademicProfileSerializer}
    )
    def patch(self, request):
        profile = self._get_profile(request)
        serializer = StudentAcademicProfileSerializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success_response(data=serializer.data, message="Student profile updated successfully")


class StudentNumberParseView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = StudentAcademicProfileSerializer

    @extend_schema(tags=["Student Number Parsing"])
    def get(self, request):
        parsed = StudentNumberParser.parse(request.query_params.get("student_number", ""))
        return success_response(data=parsed)
