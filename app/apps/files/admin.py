from django.contrib import admin

from .models import FileAccessTicket, FileResource


@admin.register(FileResource)
class FileResourceAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "visibility", "file_type", "pages_count", "uploaded_by", "is_active", "is_printable", "created_at")
    list_filter = ("visibility", "is_active", "is_printable", "university", "faculty", "major", "academic_year")
    search_fields = ("title", "description", "sha256", "uploaded_by__email")
    autocomplete_fields = ("uploaded_by", "university", "faculty", "major", "academic_year", "semester", "subject", "group")
    readonly_fields = ("file_type", "file_size", "pages_count", "sha256", "created_at", "updated_at")


@admin.register(FileAccessTicket)
class FileAccessTicketAdmin(admin.ModelAdmin):
    list_display = ("id", "file_resource", "user", "purpose", "expires_at", "use_count", "max_uses")
    readonly_fields = ("token", "file_resource", "user", "purpose", "expires_at", "max_uses", "use_count", "issued_ip_hash", "created_at", "updated_at")
