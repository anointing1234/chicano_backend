from rest_framework import serializers

from apps.accounts.serializers import UserSerializer

from .models import CustomerProfile, EmergencyContact, SavedPlace


class CustomerProfileSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)

    class Meta:
        model = CustomerProfile
        fields = ["id", "user", "rating_avg", "rating_count", "total_trips", "referral_code", "data_saver"]
        read_only_fields = ["id", "user", "rating_avg", "rating_count", "total_trips", "referral_code"]


class SavedPlaceSerializer(serializers.ModelSerializer):
    class Meta:
        model = SavedPlace
        fields = ["id", "label", "name", "address", "lat", "lng", "place_id", "note", "created_at"]
        read_only_fields = ["id", "created_at"]


class EmergencyContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmergencyContact
        fields = ["id", "name", "phone", "relationship"]
        read_only_fields = ["id"]

    def validate_phone(self, value):
        from apps.accounts.services import normalize_phone
        return normalize_phone(value)
