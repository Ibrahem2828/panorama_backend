from django.contrib import admin

from .models import Message, MessageAttachmentAccessTicket, MessageReport


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("id", "group", "sender", "message_type", "is_deleted", "created_at")
    list_filter = ("message_type", "is_deleted", "created_at")
    search_fields = ("content", "sender__email", "group__name")
    autocomplete_fields = ("group", "sender", "reply_to", "deleted_by")
    readonly_fields = ("created_at", "updated_at", "deleted_at")


@admin.register(MessageReport)
class MessageReportAdmin(admin.ModelAdmin):
    list_display = ("id", "message", "reported_by", "status", "created_at")
    list_filter = ("status", "created_at")
    autocomplete_fields = ("message", "reported_by")
    readonly_fields = ("created_at", "updated_at")

admin.site.register(MessageAttachmentAccessTicket)
