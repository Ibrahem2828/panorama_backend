from django.contrib import admin

from .models import SupportTicket, SupportTicketMessage


class SupportTicketMessageInline(admin.TabularInline):
    model = SupportTicketMessage
    extra = 0
    readonly_fields = ("created_at", "updated_at")


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ("id", "subject", "user", "category", "status", "priority", "assigned_to", "created_at")
    list_filter = ("category", "status", "priority", "assigned_to")
    search_fields = ("subject", "user__full_name", "user__email")
    autocomplete_fields = ("user", "assigned_to")
    readonly_fields = ("created_at", "updated_at", "closed_at")
    inlines = [SupportTicketMessageInline]
