from django.contrib import admin

from .models import AcademicYear, Faculty, Major, Semester, Subject, University


@admin.register(University)
class UniversityAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "code", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "code")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Faculty)
class FacultyAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "code", "university", "is_active")
    list_filter = ("university", "is_active")
    search_fields = ("name", "code", "university__name")
    autocomplete_fields = ("university",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(Major)
class MajorAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "code", "faculty", "is_active")
    list_filter = ("faculty__university", "faculty", "is_active")
    search_fields = ("name", "code", "faculty__name")
    autocomplete_fields = ("faculty",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(AcademicYear)
class AcademicYearAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(Semester)
class SemesterAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "code", "major", "academic_year", "semester", "is_active")
    list_filter = ("major__faculty__university", "major", "academic_year", "semester", "is_active")
    search_fields = ("name", "code", "major__name")
    autocomplete_fields = ("major", "academic_year", "semester")
    readonly_fields = ("created_at", "updated_at")
