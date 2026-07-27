from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("id", "actor", "action", "target_type", "target_id", "created_at")
    list_filter = ("action", "target_type", "created_at")
    search_fields = ("actor__email", "actor__full_name", "action", "target_type", "target_id")
    autocomplete_fields = ("actor",)
    readonly_fields = ("created_at", "updated_at")
