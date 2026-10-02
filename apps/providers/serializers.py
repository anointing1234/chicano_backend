from rest_framework import serializers

from apps.accounts.serializers import UserSerializer
from apps.core.models import Service

from .models import ProviderDocument, ProviderProfile, Vehicle


class VehicleSerializer(serializers.ModelSerializer):
    ride_types = serializers.SlugRelatedField(slug_field="code", many=True, read_only=True)

    class Meta:
        model = Vehicle
        fields = ["id", "kind", "make", "model", "year", "color", "plate_number", "seats", "ride_types",
                  "has_rider_helmet", "has_delivery_box", "has_reflective_vest", "has_passenger_helmet",
                  "status", "is_active", "created_at"]
        read_only_fields = ["id", "kind", "ride_types", "status", "is_active", "created_at"]

    def validate_plate_number(self, value):
        return value.upper().strip()


class VehiclePublicSerializer(serializers.ModelSerializer):
    """What a customer sees about the vehicle on their trip."""

    class Meta:
        model = Vehicle
        fields = ["kind", "make", "model", "color", "plate_number", "seats"]


class ProviderProfileSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    kind = serializers.CharField(read_only=True, help_text='"driver" (cars) or "rider" (bikes).')
    acceptance_rate = serializers.FloatField(read_only=True)
    cancellation_rate = serializers.FloatField(read_only=True)
    active_vehicle = serializers.SerializerMethodField()

    class Meta:
        model = ProviderProfile
        fields = ["id", "user", "service", "kind", "status", "status_reason", "city", "date_of_birth", "is_online",
                  "rating_avg", "rating_count", "total_trips", "acceptance_rate", "cancellation_rate",
                  "bank_name", "bank_account_number", "bank_account_name", "active_vehicle", "approved_at"]
        read_only_fields = ["id", "user", "service", "kind", "status", "status_reason", "is_online", "rating_avg",
                            "rating_count", "total_trips", "approved_at"]

    def get_active_vehicle(self, obj) -> dict | None:
        v = obj.vehicles.filter(is_active=True).first()
        return VehicleSerializer(v).data if v else None


class ProviderPublicSerializer(serializers.ModelSerializer):
    """What a customer sees about their driver/rider (no bank details, no phone: calls are masked)."""
    first_name = serializers.CharField(source="user.first_name")
    last_initial = serializers.SerializerMethodField()
    photo = serializers.ImageField(source="user.photo", read_only=True)
    kind = serializers.CharField(read_only=True)

    class Meta:
        model = ProviderProfile
        fields = ["id", "first_name", "last_initial", "photo", "kind", "rating_avg", "total_trips"]

    def get_last_initial(self, obj) -> str:
        return (obj.user.last_name[:1] + ".") if obj.user.last_name else ""


class ApplySerializer(serializers.Serializer):
    service = serializers.ChoiceField(choices=Service.choices, help_text="car = Driver app · Cars, bike = Rider app · Bikes")
    city = serializers.CharField(default="Lagos")
    first_name = serializers.CharField(required=False)
    last_name = serializers.CharField(required=False)


class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProviderDocument
        fields = ["id", "doc_type", "file", "back_file", "number", "expires_at", "status", "rejection_reason", "reviewed_at", "created_at"]
        read_only_fields = ["id", "status", "rejection_reason", "reviewed_at", "created_at"]


class OnboardingStepSerializer(serializers.Serializer):
    key = serializers.CharField()
    title = serializers.CharField()
    state = serializers.ChoiceField(choices=["done", "in_review", "action_needed", "todo"])
    documents = serializers.ListField(child=serializers.DictField(), required=False)


class OnboardingSerializer(serializers.Serializer):
    status = serializers.CharField()
    service = serializers.CharField()
    kind = serializers.CharField()
    steps_done = serializers.IntegerField()
    steps_total = serializers.IntegerField()
    steps = OnboardingStepSerializer(many=True)
    can_go_online = serializers.BooleanField()


class StatusSerializer(serializers.Serializer):
    is_online = serializers.BooleanField()


class LocationSerializer(serializers.Serializer):
    lat = serializers.DecimalField(max_digits=9, decimal_places=6)
    lng = serializers.DecimalField(max_digits=9, decimal_places=6)
    heading = serializers.IntegerField(required=False, min_value=0, max_value=359, allow_null=True)
