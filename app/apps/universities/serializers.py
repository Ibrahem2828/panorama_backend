from rest_framework import serializers

from .models import AcademicYear, Faculty, Major, Semester, Subject, University, validate_academic_hierarchy


class UniversitySerializer(serializers.ModelSerializer):
    class Meta:
        model = University
        fields = ["id", "name", "code", "description", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class FacultySerializer(serializers.ModelSerializer):
    university_name = serializers.CharField(source="university.name", read_only=True)

    class Meta:
        model = Faculty
        fields = ["id", "university", "university_name", "name", "code", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class MajorSerializer(serializers.ModelSerializer):
    faculty_name = serializers.CharField(source="faculty.name", read_only=True)
    university = serializers.IntegerField(source="faculty.university_id", read_only=True)

    class Meta:
        model = Major
        fields = ["id", "faculty", "faculty_name", "university", "name", "code", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class AcademicYearSerializer(serializers.ModelSerializer):
    class Meta:
        model = AcademicYear
        fields = ["id", "name", "order", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class SemesterSerializer(serializers.ModelSerializer):
    class Meta:
        model = Semester
        fields = ["id", "name", "order", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class SubjectSerializer(serializers.ModelSerializer):
    major_name = serializers.CharField(source="major.name", read_only=True)
    academic_year_name = serializers.CharField(source="academic_year.name", read_only=True)
    semester_name = serializers.CharField(source="semester.name", read_only=True)

    class Meta:
        model = Subject
        fields = [
            "id",
            "major",
            "major_name",
            "academic_year",
            "academic_year_name",
            "semester",
            "semester_name",
            "name",
            "code",
            "description",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        validate_academic_hierarchy(
            major=attrs.get("major") or getattr(self.instance, "major", None),
            academic_year=attrs.get("academic_year") or getattr(self.instance, "academic_year", None),
            semester=attrs.get("semester") or getattr(self.instance, "semester", None),
        )
        return attrs
