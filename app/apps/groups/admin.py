from django.contrib import admin

from .models import Group, GroupMembership


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "university", "faculty", "major", "is_active", "requires_approval", "created_at")
    list_filter = ("university", "faculty", "major", "academic_year", "semester", "is_active", "requires_approval")
    search_fields = ("name", "description")
    autocomplete_fields = ("university", "faculty", "major", "academic_year", "semester", "subject", "created_by")
    readonly_fields = ("created_at", "updated_at")


@admin.register(GroupMembership)
class GroupMembershipAdmin(admin.ModelAdmin):
    list_display = ("id", "group", "user", "role", "status", "joined_at", "created_at")
    list_filter = ("status", "role", "group")
    search_fields = ("group__name", "user__full_name", "user__email")
    autocomplete_fields = ("group", "user", "reviewed_by")
    readonly_fields = ("created_at", "updated_at", "reviewed_at", "joined_at")
