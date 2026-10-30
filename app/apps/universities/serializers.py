from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import AcademicYear, Faculty, Major, Semester, Subject, University


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
        fields = [
            "id",
            "faculty",
            "faculty_name",
            "university",
            "name",
            "code",
            "is_active",
            "created_at",
            "updated_at",
        ]
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


class SubjectFacultySerializer(serializers.ModelSerializer):
    """Compact faculty summary embedded in a subject."""

    university = UniversitySerializer(read_only=True)

    class Meta:
        model = Faculty
        fields = ["id", "name", "code", "university"]
        read_only_fields = fields


class SubjectSerializer(serializers.ModelSerializer):
    """A subject belongs to a faculty; ``major`` is an optional specialization tag.

    On write, ``faculty`` is required unless ``major`` is given (the faculty is then derived from the major).
    """

    faculty = serializers.PrimaryKeyRelatedField(queryset=Faculty.objects.filter(is_deleted=False), required=False)
    faculty_detail = SubjectFacultySerializer(source="faculty", read_only=True)
    faculty_name = serializers.CharField(source="faculty.name", read_only=True)
    major = serializers.PrimaryKeyRelatedField(
        queryset=Major.objects.filter(is_deleted=False), required=False, allow_null=True
    )
    major_detail = MajorSerializer(source="major", read_only=True, allow_null=True)
    major_name = serializers.SerializerMethodField()
    academic_year_name = serializers.CharField(source="academic_year.name", read_only=True)
    semester_name = serializers.CharField(source="semester.name", read_only=True)

    class Meta:
        model = Subject
        fields = [
            "id",
            "faculty",
            "faculty_name",
            "faculty_detail",
            "major",
            "major_name",
            "major_detail",
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
        # Uniqueness (name per faculty/major/year/semester, code per faculty) is enforced in ``validate``/the
        # database; the auto validators would require ``major`` and break the NULL-major (faculty-level) case.
        validators: list = []

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_major_name(self, obj):
        return obj.major.name if obj.major_id else None

    def validate(self, attrs):
        instance = self.instance
        major = attrs["major"] if "major" in attrs else getattr(instance, "major", None)
        faculty = attrs.get("faculty") or getattr(instance, "faculty", None)
        if faculty is None and major is not None:
            faculty = major.faculty
            attrs["faculty"] = faculty
        if faculty is None:
            raise serializers.ValidationError({"faculty": "This field is required unless a major is given."})
        academic_year = attrs.get("academic_year") or getattr(instance, "academic_year", None)
        semester = attrs.get("semester") or getattr(instance, "semester", None)
        if major is not None and major.faculty_id != faculty.id:
            raise serializers.ValidationError({"major": "Major does not belong to the selected faculty."})
        self._validate_unique(
            faculty=faculty,
            major=major,
            academic_year=academic_year,
            semester=semester,
            name=attrs.get("name", getattr(instance, "name", "")),
            code=(attrs["code"] if "code" in attrs else getattr(instance, "code", "")).upper().strip(),
        )
        return attrs

    def _validate_unique(self, *, faculty, major, academic_year, semester, name, code) -> None:
        # Soft-deleted rows still occupy the DB constraints, so they are matched too.
        others = Subject.objects.exclude(pk=self.instance.pk) if self.instance is not None else Subject.objects.all()
        if others.filter(
            faculty=faculty, major=major, academic_year=academic_year, semester=semester, name=name
        ).exists():
            raise serializers.ValidationError(
                {"name": "A subject with this name already exists for this faculty, major, year and semester."}
            )
        if code and others.filter(faculty=faculty, code=code).exists():
            raise serializers.ValidationError({"code": "A subject with this code already exists in this faculty."})


class DashboardSubjectSerializer(SubjectSerializer):
    """The dashboard list row: the subject plus how much content hangs off it (annotated counts, no per-row queries)."""

    groups_count = serializers.SerializerMethodField()
    lectures_count = serializers.SerializerMethodField()
    files_count = serializers.SerializerMethodField()

    class Meta(SubjectSerializer.Meta):
        fields = SubjectSerializer.Meta.fields + ["groups_count", "lectures_count", "files_count"]
        read_only_fields = SubjectSerializer.Meta.read_only_fields + ["groups_count", "lectures_count", "files_count"]

    @staticmethod
    def _count(obj, attr: str, related: str) -> int:
        value = getattr(obj, attr, None)  # annotated on list/retrieve; a freshly created/updated row counts lazily
        if value is not None:
            return value
        return getattr(obj, related).filter(is_deleted=False).count()

    @extend_schema_field(serializers.IntegerField())
    def get_groups_count(self, obj):
        return self._count(obj, "groups_count", "groups")

    @extend_schema_field(serializers.IntegerField())
    def get_lectures_count(self, obj):
        return self._count(obj, "lectures_count", "lectures")

    @extend_schema_field(serializers.IntegerField())
    def get_files_count(self, obj):
        return self._count(obj, "files_count", "files")
