from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read-only: the audit trail is append-only and can only be removed by the retention task."""

    list_display = ("id", "actor", "action", "target_type", "target_id", "created_at")
    list_filter = ("action", "target_type", "created_at")
    search_fields = ("actor__email", "actor__full_name", "action", "target_type", "target_id")
    raw_id_fields = ("actor",)

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
