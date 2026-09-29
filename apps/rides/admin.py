from django.contrib import admin

from .models import Rating, Ride, RideEvent, RideOffer, RideStop


class RideEventInline(admin.TabularInline):
    model = RideEvent
    extra = 0
    readonly_fields = ["event", "actor", "data", "created_at"]


@admin.register(Ride)
class RideAdmin(admin.ModelAdmin):
    list_display = ["id", "service", "status", "customer", "provider", "total_amount", "payment_method", "created_at"]
    list_filter = ["service", "status", "payment_method", "needs_manual_dispatch"]
    inlines = [RideEventInline]


admin.site.register([RideStop, RideOffer, Rating])
