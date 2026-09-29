"""
Pricing: ride types, fare rules, service zones and locked fare quotes.

    RideType      what the customer picks: CAR_STANDARD, CAR_XL, CAR_PREMIUM, BIKE
    FareRule      how a ride type is priced in a city (+ platform commission)
    ServiceZone   where a service operates (bikes are restricted in parts of Lagos)
    FareQuote     an upfront price shown to the customer, locked for 10 minutes.
                  Booking always references a quote, so the price the user saw is the price charged
                  (unless they add stops or change the destination mid-trip).

All money fields are integers in kobo (₦1 = 100 kobo).
"""
from django.conf import settings
from django.db import models

from apps.core.models import BaseModel, Service


class RideType(BaseModel):
    code = models.SlugField(max_length=30, unique=True, help_text="Stable code used by the apps, e.g. car_standard, car_xl, car_premium, bike.")
    service = models.CharField(max_length=4, choices=Service.choices, db_index=True)
    name = models.CharField(max_length=40)
    description = models.CharField(max_length=120, blank=True)
    seats = models.PositiveSmallIntegerField(default=4)
    min_vehicle_year = models.PositiveSmallIntegerField(null=True, blank=True, help_text="e.g. Premium requires 2019+.")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta(BaseModel.Meta):
        ordering = ["service", "sort_order"]

    def __str__(self):
        return self.name


class FareRule(BaseModel):
    ride_type = models.ForeignKey(RideType, on_delete=models.CASCADE, related_name="fare_rules")
    city = models.CharField(max_length=60, default="Lagos")
    base_amount = models.PositiveIntegerField(help_text="kobo")
    per_km_amount = models.PositiveIntegerField(help_text="kobo per km")
    per_min_amount = models.PositiveIntegerField(help_text="kobo per minute")
    minimum_amount = models.PositiveIntegerField(help_text="kobo")
    booking_fee_amount = models.PositiveIntegerField(default=0, help_text="kobo")
    cancellation_fee_amount = models.PositiveIntegerField(default=0, help_text="kobo, charged when the customer cancels after the grace period")
    cancellation_grace_seconds = models.PositiveIntegerField(default=120)
    free_wait_seconds = models.PositiveIntegerField(default=300)
    wait_per_min_amount = models.PositiveIntegerField(default=0, help_text="kobo per minute after the free wait")
    commission_percent = models.DecimalField(max_digits=5, decimal_places=2, default=15, help_text="Platform commission on the fare.")
    is_active = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        constraints = [models.UniqueConstraint(fields=["ride_type", "city"], condition=models.Q(is_active=True), name="one_active_rule_per_city")]


class ServiceZone(BaseModel):
    """
    Rectangular service area (simple and fast). Replace with polygons (PostGIS) when needed.
    A ride is allowed only if BOTH pickup and drop-off are inside an active zone for its service.
    """
    service = models.CharField(max_length=4, choices=Service.choices)
    name = models.CharField(max_length=80)
    city = models.CharField(max_length=60, default="Lagos")
    min_lat = models.DecimalField(max_digits=9, decimal_places=6)
    max_lat = models.DecimalField(max_digits=9, decimal_places=6)
    min_lng = models.DecimalField(max_digits=9, decimal_places=6)
    max_lng = models.DecimalField(max_digits=9, decimal_places=6)
    is_active = models.BooleanField(default=True)

    def contains(self, lat, lng) -> bool:
        return self.min_lat <= lat <= self.max_lat and self.min_lng <= lng <= self.max_lng


class FareQuote(BaseModel):
    """A locked upfront price. Created by POST /rides/estimate/, consumed by POST /rides/."""
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="fare_quotes")
    ride_type = models.ForeignKey(RideType, on_delete=models.PROTECT)
    pickup_lat = models.DecimalField(max_digits=9, decimal_places=6)
    pickup_lng = models.DecimalField(max_digits=9, decimal_places=6)
    dropoff_lat = models.DecimalField(max_digits=9, decimal_places=6)
    dropoff_lng = models.DecimalField(max_digits=9, decimal_places=6)
    stops = models.JSONField(default=list, blank=True, help_text='[{"lat":..,"lng":..,"address":".."}]')
    distance_m = models.PositiveIntegerField()
    duration_s = models.PositiveIntegerField()
    gross_amount = models.PositiveIntegerField(help_text="Fare before discount (kobo).")
    discount_amount = models.PositiveIntegerField(default=0)
    total_amount = models.PositiveIntegerField(help_text="What the customer pays (kobo).")
    breakdown = models.JSONField(default=dict)
    promo = models.ForeignKey("payments.PromoCode", null=True, blank=True, on_delete=models.SET_NULL)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
