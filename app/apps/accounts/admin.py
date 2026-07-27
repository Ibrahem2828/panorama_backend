from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import OTPCode, StudentProfile, User, UserPermissionOverride


class UserPermissionOverrideInline(admin.TabularInline):
    model = UserPermissionOverride
    fk_name = "user"
    extra = 0
    readonly_fields = ("granted_by", "created_at", "updated_at")


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    model = User
    list_display = ("id", "full_name", "email", "phone_number", "role", "is_email_verified", "is_phone_verified", "is_active", "date_joined")
    list_filter = ("role", "is_phone_verified", "is_email_verified", "is_active", "is_staff", "is_superuser")
    search_fields = ("full_name", "email", "phone_number", "username")
    ordering = ("-date_joined",)
    readonly_fields = ("date_joined", "last_login", "last_password_change_at", "created_at", "updated_at")
    inlines = [UserPermissionOverrideInline]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("full_name", "username", "phone_number")}),
        ("Role and verification", {"fields": ("role", "is_phone_verified", "is_email_verified")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "last_password_change_at", "date_joined", "created_at", "updated_at")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("full_name", "email", "phone_number", "role", "password1", "password2", "is_staff", "is_superuser")}),)


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "student_number", "verification_status", "created_at")
    list_filter = ("verification_status",)
    search_fields = ("user__full_name", "user__email", "student_number")
    readonly_fields = ("created_at", "updated_at")


@admin.register(OTPCode)
class OTPCodeAdmin(admin.ModelAdmin):
    list_display = ("id", "delivery_channel", "email", "phone_number", "purpose", "user", "is_used", "attempts_count", "expires_at")
    list_filter = ("delivery_channel", "purpose", "is_used", "locked_at", "created_at")
    search_fields = ("email", "phone_number", "user__email")
    readonly_fields = ("code_hash", "created_at", "updated_at")
