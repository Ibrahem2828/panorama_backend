from django.contrib import admin

from .models import Announcement


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "target_user_type", "is_active", "starts_at", "ends_at", "created_at")
    list_filter = ("target_user_type", "is_active", "target_university", "target_faculty", "target_major")
    search_fields = ("title", "description")
    autocomplete_fields = (
        "created_by",
        "target_university",
        "target_faculty",
        "target_major",
        "target_academic_year",
        "target_semester",
    )
    readonly_fields = ("created_at", "updated_at")
