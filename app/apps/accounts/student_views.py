from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, serializers
from rest_framework.exceptions import ValidationError
from rest_framework.views import APIView

from apps.accounts.choices import UserRole
from apps.accounts.permissions import IsStudent
from apps.accounts.serializers import StudentAcademicProfileSerializer
from apps.accounts.student_number import StudentNumberParser
from apps.common.responses import success_response
from apps.universities.terms import confirm_student_term


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
        serializer = StudentAcademicProfileSerializer(
            profile, data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success_response(data=serializer.data, message="Student profile updated successfully")


class TermConfirmationSerializer(serializers.Serializer):
    promoted = serializers.BooleanField()
    academic_year = serializers.IntegerField(min_value=1, allow_null=True, required=False, default=None)
    semester = serializers.IntegerField(min_value=1, allow_null=True, required=False, default=None)


class StudentTermConfirmationView(APIView):
    """``POST students/me/term-confirmation/``: the student's own answer to "did you move to the next year?"."""

    permission_classes = [IsStudent]
    serializer_class = TermConfirmationSerializer

    @extend_schema(
        tags=["Students"], request=TermConfirmationSerializer, responses={200: StudentAcademicProfileSerializer}
    )
    def post(self, request):
        serializer = TermConfirmationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile = confirm_student_term(
            request.user,
            promoted=serializer.validated_data["promoted"],
            academic_year_id=serializer.validated_data["academic_year"],
            semester_id=serializer.validated_data["semester"],
            request=request,
        )
        return success_response(
            data=StudentAcademicProfileSerializer(profile).data,
            message="Term confirmation saved",
            request=request,
            code="TERM_CONFIRMED",
        )


class StudentNumberParseView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = StudentAcademicProfileSerializer

    @extend_schema(tags=["Student Number Parsing"])
    def get(self, request):
        try:
            parsed = StudentNumberParser.parse(request.query_params.get("student_number", ""))
        except DjangoValidationError as exc:
            raise ValidationError({"student_number": exc.messages}) from exc
        return success_response(data=parsed)
