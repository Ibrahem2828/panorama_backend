from django.contrib import admin

from .models import (
    PrintBindingPrice,
    PrintItemAccessTicket,
    PrintOrder,
    PrintOrderItem,
    PrintOrderStatusHistory,
    PrintPickupLocation,
    PrintPricingRule,
)


class PrintOrderItemInline(admin.TabularInline):
    model = PrintOrderItem
    extra = 0
    readonly_fields = ("file_type", "file_size", "pages_count", "sheets_count", "unit_price", "binding_price", "price", "pricing_snapshot", "created_at", "updated_at")


@admin.register(PrintOrder)
class PrintOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "status", "priority", "assigned_to", "total_price", "currency", "created_at")
    list_filter = ("status", "priority", "assigned_to", "currency", "created_at")
    search_fields = ("id", "user__full_name", "user__email", "user__phone_number")
    autocomplete_fields = ("user", "assigned_to")
    readonly_fields = ("total_price", "currency", "pricing_snapshot", "price_calculated_at", "created_at", "updated_at", "completed_at", "cancelled_at")
    inlines = [PrintOrderItemInline]


@admin.register(PrintOrderStatusHistory)
class PrintOrderStatusHistoryAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "old_status", "new_status", "changed_by", "created_at")
    list_filter = ("old_status", "new_status", "created_at")
    readonly_fields = ("public_note", "internal_note", "created_at", "updated_at")


admin.site.register(PrintPricingRule)
admin.site.register(PrintBindingPrice)
admin.site.register(PrintPickupLocation)
admin.site.register(PrintItemAccessTicket)
