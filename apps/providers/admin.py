from django.contrib import admin

from .models import ProviderDocument, ProviderProfile, Vehicle


@admin.register(ProviderProfile)
class ProviderProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "service", "status", "is_online", "rating_avg", "total_trips"]
    list_filter = ["service", "status", "is_online"]
    search_fields = ["user__phone", "user__first_name", "user__last_name"]


admin.site.register([Vehicle, ProviderDocument])
