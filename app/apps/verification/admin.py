from django.contrib import admin

from .models import VerificationCardAccessTicket, VerificationRequest


@admin.register(VerificationRequest)
class VerificationRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "student_number", "university", "major", "status", "created_at", "reviewed_at")
    list_filter = ("status", "university", "faculty", "major", "academic_year", "created_at")
    search_fields = ("user__full_name", "user__email", "user__phone_number", "student_number")
    autocomplete_fields = (
        "user",
        "student_profile",
        "university",
        "faculty",
        "major",
        "academic_year",
        "semester",
        "reviewed_by",
    )
    readonly_fields = ("created_at", "updated_at", "reviewed_at")


@admin.register(VerificationCardAccessTicket)
class VerificationCardAccessTicketAdmin(admin.ModelAdmin):
    list_display = ("id", "verification", "requested_by", "expires_at", "use_count", "max_uses")
    readonly_fields = (
        "token",
        "verification",
        "requested_by",
        "expires_at",
        "max_uses",
        "use_count",
        "revoked_at",
        "created_at",
        "updated_at",
    )
