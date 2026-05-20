from django.contrib import admin

from .models import PrintOrder, PrintOrderItem, PrintOrderStatusHistory


class PrintOrderItemInline(admin.TabularInline):
    model = PrintOrderItem
    extra = 0
    readonly_fields = ("file_type", "file_size", "created_at", "updated_at")


@admin.register(PrintOrder)
class PrintOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "status", "priority", "assigned_to", "total_price", "created_at")
    list_filter = ("status", "priority", "assigned_to", "created_at")
    search_fields = ("id", "user__full_name", "user__email", "user__phone_number")
    autocomplete_fields = ("user", "assigned_to")
    readonly_fields = ("created_at", "updated_at", "completed_at", "cancelled_at")
    inlines = [PrintOrderItemInline]


@admin.register(PrintOrderStatusHistory)
class PrintOrderStatusHistoryAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "old_status", "new_status", "changed_by", "created_at")
    list_filter = ("old_status", "new_status", "created_at")
    autocomplete_fields = ("order", "changed_by")
    readonly_fields = ("created_at", "updated_at")
