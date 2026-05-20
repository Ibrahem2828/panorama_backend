from django.core.exceptions import ValidationError
from django.db import models

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
    major = models.ForeignKey(Major, on_delete=models.CASCADE, related_name="subjects")
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name="subjects")
    semester = models.ForeignKey(Semester, on_delete=models.CASCADE, related_name="subjects")
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=32, blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["major__name", "academic_year__order", "semester__order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["major", "academic_year", "semester", "name"],
                name="unique_subject_name_per_major_year_semester",
            ),
            models.UniqueConstraint(
                fields=["major", "code"],
                condition=~models.Q(code=""),
                name="unique_subject_code_per_major",
            ),
        ]
        indexes = [
            models.Index(fields=["major", "academic_year", "semester", "is_active"]),
            models.Index(fields=["code"]),
        ]

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
        if major and subject.major_id != major.id:
            errors["subject"] = "Subject does not belong to the selected major."
        if academic_year and subject.academic_year_id != academic_year.id:
            errors["subject"] = "Subject does not belong to the selected academic year."
        if semester and subject.semester_id != semester.id:
            errors["subject"] = "Subject does not belong to the selected semester."
    if errors:
        raise ValidationError(errors)
