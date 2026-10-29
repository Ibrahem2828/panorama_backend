from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel


class University(BaseModel):
    name = models.CharField(max_length=255, unique=True)
    code = models.CharField(max_length=32, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["is_active"]), models.Index(fields=["code"])]

    def save(self, *args, **kwargs):
        self.code = self.code.upper().strip()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class Faculty(BaseModel):
    university = models.ForeignKey(University, on_delete=models.CASCADE, related_name="faculties")
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=32, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["university__name", "name"]
        constraints = [
            models.UniqueConstraint(fields=["university", "name"], name="unique_faculty_name_per_university"),
            models.UniqueConstraint(
                fields=["university", "code"],
                condition=~models.Q(code=""),
                name="unique_faculty_code_per_university",
            ),
        ]
        indexes = [models.Index(fields=["university", "is_active"]), models.Index(fields=["code"])]

    def save(self, *args, **kwargs):
        self.code = self.code.upper().strip()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.name} - {self.university.name}"


class Major(BaseModel):
    faculty = models.ForeignKey(Faculty, on_delete=models.CASCADE, related_name="majors")
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=32, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["faculty__name", "name"]
        constraints = [
            models.UniqueConstraint(fields=["faculty", "name"], name="unique_major_name_per_faculty"),
            models.UniqueConstraint(
                fields=["faculty", "code"],
                condition=~models.Q(code=""),
                name="unique_major_code_per_faculty",
            ),
        ]
        indexes = [models.Index(fields=["faculty", "is_active"]), models.Index(fields=["code"])]

    def save(self, *args, **kwargs):
        self.code = self.code.upper().strip()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.name} - {self.faculty.name}"


class AcademicYear(BaseModel):
    name = models.CharField(max_length=100, unique=True)
    order = models.PositiveSmallIntegerField(unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order"]
        indexes = [models.Index(fields=["is_active", "order"])]

    def __str__(self) -> str:
        return self.name


class Semester(BaseModel):
    name = models.CharField(max_length=100, unique=True)
    order = models.PositiveSmallIntegerField(unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order"]
        indexes = [models.Index(fields=["is_active", "order"])]

    def __str__(self) -> str:
        return self.name


class Subject(BaseModel):
    """A subject belongs to a faculty; ``major`` is an optional specialization tag.

    Specializations only start in year 3 and only some subjects belong to one, so ``major`` may be empty.
    """

    faculty = models.ForeignKey(Faculty, on_delete=models.CASCADE, related_name="subjects")
    major = models.ForeignKey(Major, on_delete=models.CASCADE, related_name="subjects", null=True, blank=True)
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name="subjects")
    semester = models.ForeignKey(Semester, on_delete=models.CASCADE, related_name="subjects")
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=32, blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["faculty__name", "academic_year__order", "semester__order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["faculty", "major", "academic_year", "semester", "name"],
                condition=models.Q(major__isnull=False),
                name="unique_subject_name_per_major_year_semester",
            ),
            models.UniqueConstraint(
                fields=["faculty", "academic_year", "semester", "name"],
                condition=models.Q(major__isnull=True),
                name="unique_subject_name_per_faculty_year_semester_no_major",
            ),
            models.UniqueConstraint(
                fields=["faculty", "code"],
                condition=~models.Q(code=""),
                name="unique_subject_code_per_faculty",
            ),
        ]
        indexes = [
            models.Index(fields=["faculty", "academic_year", "semester", "is_active"]),
            models.Index(fields=["faculty", "major", "is_active"]),
            models.Index(fields=["code"]),
        ]

    def clean(self):
        super().clean()
        if self.major is not None and self.faculty_id and self.major.faculty_id != self.faculty_id:
            raise ValidationError({"major": "Major does not belong to the subject's faculty."})

    def save(self, *args, **kwargs):
        self.code = self.code.upper().strip()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


def validate_academic_hierarchy(
    *,
    university: University | None = None,
    faculty: Faculty | None = None,
    major: Major | None = None,
    academic_year: AcademicYear | None = None,
    semester: Semester | None = None,
    subject: Subject | None = None,
) -> None:
    errors = {}
    if faculty and university and faculty.university_id != university.id:
        errors["faculty"] = "Faculty does not belong to the selected university."
    if major and faculty and major.faculty_id != faculty.id:
        errors["major"] = "Major does not belong to the selected faculty."
    if major and university and major.faculty.university_id != university.id:
        errors["major"] = "Major does not belong to the selected university."
    if subject:
        # A group/file may pin only some of the hierarchy: compare only what is pinned.
        if faculty and subject.faculty_id != faculty.id:
            errors["subject"] = "Subject does not belong to the selected faculty."
        if major and subject.major_id is not None and subject.major_id != major.id:
            errors["subject"] = "Subject does not belong to the selected major."
        if academic_year and subject.academic_year_id != academic_year.id:
            errors["subject"] = "Subject does not belong to the selected academic year."
        if semester and subject.semester_id != semester.id:
            errors["subject"] = "Subject does not belong to the selected semester."
    if errors:
        raise ValidationError(errors)


class CurrentTerm(BaseModel):
    """The semester the platform is in right now. A single row (pk=1), set by the administrator."""

    SINGLETON_PK = 1

    semester = models.ForeignKey(Semester, on_delete=models.PROTECT, related_name="+")
    label = models.CharField(max_length=100, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    set_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    def __str__(self) -> str:
        return self.label or self.semester.name


class TermRollover(BaseModel):
    """One "end semester" run: what it removed, so it can be exported (WhatsApp list) and, if it is the latest, undone."""

    performed_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="term_rollovers"
    )
    from_semester = models.ForeignKey(Semester, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    to_semester = models.ForeignKey(Semester, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    from_label = models.CharField(max_length=100, blank=True)
    to_label = models.CharField(max_length=100, blank=True)
    disable_whatsapp_links = models.BooleanField(default=False)
    memberships_removed = models.PositiveIntegerField(default=0)
    students_affected = models.PositiveIntegerField(default=0)
    #: Per affected group: ``{group_id, name, subject, whatsapp_active, members_to_remove, whatsapp_disabled}``.
    snapshot = models.JSONField(default=list, blank=True)
    previous_started_at = models.DateTimeField(null=True, blank=True)
    undone_at = models.DateTimeField(null=True, blank=True)
    undone_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"rollover {self.pk}: {self.from_label} -> {self.to_label}"
