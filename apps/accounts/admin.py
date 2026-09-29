"""Django admin (engineering use only). Ops staff use the Super Admin web app."""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Device, OTPCode, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ["-created_at"]
    list_display = ["phone", "full_name", "email", "status", "staff_role", "created_at"]
    list_filter = ["status", "is_staff", "staff_role"]
    search_fields = ["phone", "email", "first_name", "last_name"]
    fieldsets = (
        (None, {"fields": ("phone", "password")}),
        ("Profile", {"fields": ("first_name", "last_name", "email", "photo")}),
        ("Status", {"fields": ("status", "status_reason", "phone_verified_at", "is_active")}),
        ("Staff", {"fields": ("is_staff", "is_superuser", "staff_role", "groups", "user_permissions")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("phone", "password1", "password2")}),)


admin.site.register(OTPCode)
admin.site.register(Device)
