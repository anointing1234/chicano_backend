"""Request/response shapes for the Auth endpoints (these become the OpenAPI schemas)."""
from rest_framework import serializers

from .models import AppKind, Device, User


class OtpRequestSerializer(serializers.Serializer):
    phone = serializers.CharField(help_text="Nigerian number in any common format: 08034125567, +2348034125567.")


class OtpRequestResponseSerializer(serializers.Serializer):
    phone = serializers.CharField(help_text="Normalised E.164 number. Send this exact value to /otp/verify/.")
    expires_in_seconds = serializers.IntegerField()
    resend_after_seconds = serializers.IntegerField()
    debug_code = serializers.CharField(required=False, help_text="DEV ONLY (OTP_DEBUG_RETURN_CODE=true). Never present in production.")


class OtpVerifySerializer(serializers.Serializer):
    phone = serializers.CharField()
    code = serializers.RegexField(r"^\d{6}$", help_text="6-digit SMS code.")
    app = serializers.ChoiceField(
        choices=AppKind.choices,
        help_text=(
            "Which app is logging in. user_cars / user_bikes create a customer profile on first login. "
            "driver_cars / rider_bikes do not create anything: call POST /provider/apply/ next if "
            "`roles` has no driver/rider."
        ),
    )


class UserSerializer(serializers.ModelSerializer):
    """The logged-in user (also nested elsewhere as the public-safe subset)."""
    roles = serializers.ListField(child=serializers.CharField(), read_only=True,
                                  help_text='Any of "customer", "driver", "rider", "staff".')
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = ["id", "phone", "email", "first_name", "last_name", "full_name", "photo", "status", "roles",
                  "phone_verified_at", "date_joined"]
        read_only_fields = ["id", "phone", "status", "roles", "phone_verified_at", "date_joined"]


class UserUpdateSerializer(serializers.ModelSerializer):
    """PATCH /auth/me/ accepts these fields. Use multipart to upload `photo`."""

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "photo"]


class AuthResponseSerializer(serializers.Serializer):
    access = serializers.CharField(help_text="Short-lived (30 min). Send as `Authorization: Bearer <access>`.")
    refresh = serializers.CharField(help_text="Long-lived (30 days). Store in expo-secure-store; use to get new access tokens.")
    is_new_user = serializers.BooleanField(help_text="True on first login: show the profile-setup screen.")
    user = UserSerializer()


class RefreshSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class TokenPairSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField(help_text="A NEW refresh token (rotation). Replace the stored one.")


class DeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Device
        fields = ["id", "app", "platform", "push_token", "app_version", "last_seen_at"]
        read_only_fields = ["id", "last_seen_at"]


class StaffLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})


class StaffUserSerializer(UserSerializer):
    class Meta(UserSerializer.Meta):
        fields = UserSerializer.Meta.fields + ["staff_role"]


class StaffAuthResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    user = StaffUserSerializer()
