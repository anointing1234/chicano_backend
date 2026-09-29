from rest_framework import serializers

from .models import FareQuote, FareRule, RideType, ServiceZone


class RideTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = RideType
        fields = ["id", "code", "service", "name", "description", "seats", "min_vehicle_year", "is_active", "sort_order"]


class FareRuleSerializer(serializers.ModelSerializer):
    ride_type_code = serializers.CharField(source="ride_type.code", read_only=True)

    class Meta:
        model = FareRule
        fields = ["id", "ride_type", "ride_type_code", "city", "base_amount", "per_km_amount", "per_min_amount", "minimum_amount",
                  "booking_fee_amount", "cancellation_fee_amount", "cancellation_grace_seconds", "free_wait_seconds",
                  "wait_per_min_amount", "commission_percent", "is_active"]


class ServiceZoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceZone
        fields = ["id", "service", "name", "city", "min_lat", "max_lat", "min_lng", "max_lng", "is_active"]


class FareQuoteSerializer(serializers.ModelSerializer):
    """One priced option in the 'Choose a ride' sheet."""
    ride_type = RideTypeSerializer(read_only=True)
    quote_id = serializers.UUIDField(source="id", read_only=True, help_text="Send this to POST /rides/ to book at this price.")
    promo_code = serializers.CharField(source="promo.code", read_only=True, default=None)

    class Meta:
        model = FareQuote
        fields = ["quote_id", "ride_type", "distance_m", "duration_s", "gross_amount", "discount_amount", "total_amount",
                  "breakdown", "promo_code", "expires_at"]
