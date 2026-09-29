"""
Ride request/response shapes.

Customer apps get `RideSerializer` (driver/rider public info + live location).
Provider apps get `ProviderTripSerializer` (customer first name, pickup note, cash to collect).
"""
from django.conf import settings
from rest_framework import serializers

from apps.core.geo import eta_seconds, haversine_km
from apps.pricing.serializers import RideTypeSerializer
from apps.providers.serializers import ProviderPublicSerializer, VehiclePublicSerializer

from .models import PaymentMethodKind, Rating, Ride, RideEvent, RideOffer, RideStatus, RideStop


class PointSerializer(serializers.Serializer):
    lat = serializers.DecimalField(max_digits=9, decimal_places=6)
    lng = serializers.DecimalField(max_digits=9, decimal_places=6)


class StopInputSerializer(PointSerializer):
    address = serializers.CharField(max_length=255)


class EstimateRequestSerializer(serializers.Serializer):
    service = serializers.ChoiceField(choices=[("car", "Car"), ("bike", "Bike")])
    pickup = PointSerializer()
    dropoff = PointSerializer()
    stops = StopInputSerializer(many=True, required=False, default=list, help_text="Cars only, max 3.")
    promo_code = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if attrs["service"] == "bike" and attrs.get("stops"):
            raise serializers.ValidationError({"stops": ["Bike rides can't have extra stops."]})
        if len(attrs.get("stops") or []) > 3:
            raise serializers.ValidationError({"stops": ["Up to 3 stops."]})
        return attrs


class RideRequestSerializer(serializers.Serializer):
    quote_id = serializers.UUIDField(help_text="From POST /rides/estimate/ (valid 10 minutes).")
    payment_method = serializers.ChoiceField(choices=PaymentMethodKind.choices)
    payment_method_id = serializers.UUIDField(required=False, allow_null=True, help_text="Saved card id when payment_method=card.")
    pickup_address = serializers.CharField(max_length=255)
    dropoff_address = serializers.CharField(max_length=255)
    pickup_note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="",
                                        help_text="Landmark for the driver, e.g. 'Blue gate, opposite GTBank'.")
    stop_addresses = serializers.ListField(child=serializers.CharField(max_length=255), required=False)
    scheduled_for = serializers.DateTimeField(required=False, allow_null=True, help_text="Cars only. ISO 8601, 30 min to 7 days ahead.")

    def validate_scheduled_for(self, value):
        from datetime import timedelta
        from django.utils import timezone
        if value and not (timezone.now() + timedelta(minutes=30) <= value <= timezone.now() + timedelta(days=7)):
            raise serializers.ValidationError("Schedule between 30 minutes and 7 days from now.")
        return value


class RideStopSerializer(serializers.ModelSerializer):
    class Meta:
        model = RideStop
        fields = ["order", "lat", "lng", "address", "completed_at"]


class ProviderLocationSerializer(serializers.Serializer):
    lat = serializers.DecimalField(max_digits=9, decimal_places=6)
    lng = serializers.DecimalField(max_digits=9, decimal_places=6)
    heading = serializers.IntegerField(allow_null=True)
    updated_at = serializers.DateTimeField()


class RideSerializer(serializers.ModelSerializer):
    """Customer view of a ride. Poll GET /rides/{id}/ every 3-5 s while `is_active` is true."""
    ride_type = RideTypeSerializer(read_only=True)
    stops = RideStopSerializer(many=True, read_only=True)
    provider = ProviderPublicSerializer(read_only=True, help_text="Driver (car) or rider (bike). Null while searching.")
    vehicle = VehiclePublicSerializer(read_only=True)
    provider_location = serializers.SerializerMethodField(help_text="Live position while accepted/arrived/in_progress.")
    eta_to_pickup_seconds = serializers.SerializerMethodField()
    is_active = serializers.BooleanField(read_only=True)
    cancellation_fee_if_cancelled_now = serializers.SerializerMethodField()
    requires_helmet = serializers.SerializerMethodField(help_text="True for bike rides: show the helmet reminder.")
    share_url = serializers.SerializerMethodField()
    my_rating = serializers.SerializerMethodField()

    class Meta:
        model = Ride
        fields = ["id", "service", "status", "is_active", "ride_type", "pickup_lat", "pickup_lng", "pickup_address", "pickup_note",
                  "dropoff_lat", "dropoff_lng", "dropoff_address", "stops", "scheduled_for",
                  "distance_m", "duration_s", "gross_amount", "discount_amount", "wait_charge_amount", "total_amount", "tip_amount",
                  "cancellation_fee_amount", "refunded_amount", "payment_method", "payment_status",
                  "provider", "vehicle", "provider_location", "eta_to_pickup_seconds", "requires_helmet",
                  "cancellation_fee_if_cancelled_now", "share_url", "my_rating",
                  "requested_at", "accepted_at", "arrived_at", "started_at", "completed_at", "cancelled_at", "cancelled_by", "cancel_reason"]

    def get_provider_location(self, obj) -> dict | None:
        p = obj.provider
        if not p or obj.status not in (RideStatus.ACCEPTED, RideStatus.ARRIVED, RideStatus.IN_PROGRESS) or p.last_lat is None:
            return None
        return {"lat": p.last_lat, "lng": p.last_lng, "heading": p.last_heading, "updated_at": p.last_location_at}

    def get_eta_to_pickup_seconds(self, obj) -> int | None:
        p = obj.provider
        if obj.status != RideStatus.ACCEPTED or not p or p.last_lat is None:
            return None
        return eta_seconds(haversine_km(p.last_lat, p.last_lng, obj.pickup_lat, obj.pickup_lng), obj.service)

    def get_cancellation_fee_if_cancelled_now(self, obj) -> int:
        from .services import cancellation_fee_if_cancelled_now
        return cancellation_fee_if_cancelled_now(obj) if obj.is_active else 0

    def get_requires_helmet(self, obj) -> bool:
        return obj.service == "bike"

    def get_share_url(self, obj) -> str:
        return f"{settings.SHARE_BASE_URL}{obj.share_token}"

    def get_my_rating(self, obj) -> int | None:
        r = obj.ratings.filter(direction="customer_to_provider").first()
        return r.stars if r else None


class RideListSerializer(serializers.ModelSerializer):
    """Compact row for the Rides tab."""
    ride_type_name = serializers.CharField(source="ride_type.name")

    class Meta:
        model = Ride
        fields = ["id", "service", "status", "ride_type_name", "pickup_address", "dropoff_address", "scheduled_for",
                  "total_amount", "tip_amount", "payment_method", "payment_status", "requested_at", "completed_at"]


class CancelSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class RateSerializer(serializers.Serializer):
    stars = serializers.IntegerField(min_value=1, max_value=5)
    tags = serializers.ListField(child=serializers.CharField(max_length=40), required=False, default=list)
    comment = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    tip_amount = serializers.IntegerField(min_value=0, required=False, default=0, help_text="kobo. Customer only.")


class TipSerializer(serializers.Serializer):
    amount = serializers.IntegerField(min_value=100, help_text="kobo (e.g. 50000 = ₦500)")


class RetryPaymentSerializer(serializers.Serializer):
    payment_method = serializers.ChoiceField(choices=PaymentMethodKind.choices)
    payment_method_id = serializers.UUIDField(required=False, allow_null=True)


class RatingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Rating
        fields = ["id", "direction", "stars", "tags", "comment", "created_at"]


# --------------------------------------------------------------------------- provider side
class CustomerPublicSerializer(serializers.Serializer):
    first_name = serializers.CharField()
    rating_avg = serializers.DecimalField(max_digits=3, decimal_places=2)
    total_trips = serializers.IntegerField()


def _customer_public(user) -> dict:
    cp = getattr(user, "customer_profile", None)
    return {"first_name": user.first_name or "Customer", "rating_avg": cp.rating_avg if cp else 5, "total_trips": cp.total_trips if cp else 0}


class OfferSerializer(serializers.ModelSerializer):
    """The incoming-request card. Show a countdown from `seconds_left`."""
    seconds_left = serializers.SerializerMethodField()
    service = serializers.CharField(source="ride.service")
    ride_type_name = serializers.CharField(source="ride.ride_type.name")
    pickup_address = serializers.CharField(source="ride.pickup_address")
    pickup_note = serializers.CharField(source="ride.pickup_note")
    pickup_lat = serializers.DecimalField(source="ride.pickup_lat", max_digits=9, decimal_places=6)
    pickup_lng = serializers.DecimalField(source="ride.pickup_lng", max_digits=9, decimal_places=6)
    dropoff_address = serializers.CharField(source="ride.dropoff_address")
    trip_distance_m = serializers.IntegerField(source="ride.distance_m")
    trip_duration_s = serializers.IntegerField(source="ride.duration_s")
    estimated_fare_amount = serializers.IntegerField(source="ride.total_amount", help_text="What the customer pays (kobo).")
    payment_method = serializers.CharField(source="ride.payment_method")
    stops_count = serializers.SerializerMethodField()
    customer = serializers.SerializerMethodField()

    class Meta:
        model = RideOffer
        fields = ["id", "ride", "status", "is_manual", "expires_at", "seconds_left", "service", "ride_type_name",
                  "distance_to_pickup_m", "eta_to_pickup_s", "pickup_address", "pickup_note", "pickup_lat", "pickup_lng",
                  "dropoff_address", "trip_distance_m", "trip_duration_s", "estimated_fare_amount", "payment_method",
                  "stops_count", "customer"]

    def get_seconds_left(self, obj) -> int:
        from django.utils import timezone
        return max(0, int((obj.expires_at - timezone.now()).total_seconds()))

    def get_stops_count(self, obj) -> int:
        return obj.ride.stops.count()

    def get_customer(self, obj) -> CustomerPublicSerializer:
        return _customer_public(obj.ride.customer)


class ProviderTripSerializer(serializers.ModelSerializer):
    """Driver/rider view of their current or past trip."""
    ride_type_name = serializers.CharField(source="ride_type.name")
    stops = RideStopSerializer(many=True, read_only=True)
    customer = serializers.SerializerMethodField()
    cash_due_amount = serializers.IntegerField(read_only=True, help_text="Cash to collect (0 for card/wallet). Includes tip.")
    helmet_required = serializers.SerializerMethodField()

    class Meta:
        model = Ride
        fields = ["id", "service", "status", "ride_type_name", "pickup_lat", "pickup_lng", "pickup_address", "pickup_note",
                  "dropoff_lat", "dropoff_lng", "dropoff_address", "stops", "distance_m", "duration_s",
                  "gross_amount", "discount_amount", "wait_charge_amount", "total_amount", "tip_amount", "commission_amount",
                  "payment_method", "payment_status", "cash_due_amount", "cash_collected_at", "customer",
                  "helmet_required", "helmet_handed_over_at", "helmet_returned_at",
                  "accepted_at", "arrived_at", "started_at", "completed_at", "cancelled_at", "cancel_reason"]

    def get_customer(self, obj) -> CustomerPublicSerializer:
        return _customer_public(obj.customer)

    def get_helmet_required(self, obj) -> bool:
        return obj.service == "bike"


class RideEventSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.full_name", default=None)

    class Meta:
        model = RideEvent
        fields = ["id", "event", "actor_name", "data", "created_at"]
