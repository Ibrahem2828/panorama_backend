from django.contrib import admin

from .models import DeviceToken, Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "title", "type", "is_read", "created_at")
    list_filter = ("type", "is_read", "created_at")
    search_fields = ("title", "body", "user__email", "user__full_name")
    autocomplete_fields = ("user",)
    readonly_fields = ("created_at", "updated_at", "read_at")


@admin.register(DeviceToken)
class DeviceTokenAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "platform", "is_active", "last_used_at", "updated_at")
    list_filter = ("platform", "is_active")
    search_fields = ("user__email", "token")
    autocomplete_fields = ("user",)
    readonly_fields = ("created_at", "updated_at", "last_used_at")
