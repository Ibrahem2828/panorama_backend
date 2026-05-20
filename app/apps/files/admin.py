from django.contrib import admin

from .models import FileResource


@admin.register(FileResource)
class FileResourceAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "visibility", "uploaded_by", "is_active", "is_printable", "created_at")
    list_filter = ("visibility", "is_active", "is_printable", "university", "faculty", "major", "academic_year")
    search_fields = ("title", "description", "uploaded_by__email")
    autocomplete_fields = (
        "uploaded_by",
        "university",
        "faculty",
        "major",
        "academic_year",
        "semester",
        "subject",
        "group",
    )
    readonly_fields = ("file_type", "file_size", "created_at", "updated_at")
