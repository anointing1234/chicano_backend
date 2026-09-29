from rest_framework import serializers

from .models import Incentive, PaymentMethod, Payout, PromoCode, ProviderLedgerEntry, Wallet, WalletTransaction


class WalletSerializer(serializers.ModelSerializer):
    class Meta:
        model = Wallet
        fields = ["balance_amount", "currency", "updated_at"]


class WalletTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WalletTransaction
        fields = ["id", "kind", "amount", "balance_after", "ride", "note", "created_at"]


class PaymentMethodSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentMethod
        fields = ["id", "brand", "last4", "exp_month", "exp_year", "is_default", "created_at"]
        read_only_fields = fields


class AddCardSerializer(serializers.Serializer):
    """The app tokenises the card with the gateway SDK first. Never send card numbers to this API."""
    gateway_token = serializers.CharField(help_text="Paystack authorization_code (or 'fail_...' to simulate a declining card in dev).")
    brand = serializers.CharField()
    last4 = serializers.RegexField(r"^\d{4}$")
    exp_month = serializers.IntegerField(min_value=1, max_value=12)
    exp_year = serializers.IntegerField(min_value=2024, max_value=2100)
    make_default = serializers.BooleanField(default=False)


class TopUpSerializer(serializers.Serializer):
    amount = serializers.IntegerField(min_value=10_000, max_value=50_000_000, help_text="kobo (₦100 – ₦500,000)")
    payment_method_id = serializers.UUIDField(help_text="Saved card to charge.")


class PromoValidateSerializer(serializers.Serializer):
    code = serializers.CharField()
    service = serializers.ChoiceField(choices=[("car", "Car"), ("bike", "Bike")])


class PromoPublicSerializer(serializers.ModelSerializer):
    class Meta:
        model = PromoCode
        fields = ["code", "description", "discount_type", "value", "max_discount_amount", "service", "valid_to"]


class PromoAdminSerializer(serializers.ModelSerializer):
    redemptions_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = PromoCode
        fields = ["id", "code", "description", "discount_type", "value", "max_discount_amount", "service", "usage_limit",
                  "per_user_limit", "first_ride_only", "budget_amount", "spent_amount", "valid_from", "valid_to", "is_active",
                  "redemptions_count", "created_at"]
        read_only_fields = ["id", "spent_amount", "redemptions_count", "created_at"]

    def validate(self, attrs):
        if attrs.get("discount_type") == "percent" and attrs.get("value", 0) > 100:
            raise serializers.ValidationError({"value": ["Percent can't exceed 100."]})
        if attrs.get("valid_from") and attrs.get("valid_to") and attrs["valid_to"] <= attrs["valid_from"]:
            raise serializers.ValidationError({"valid_to": ["Must be after the start date."]})
        return attrs


class LedgerEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = ProviderLedgerEntry
        fields = ["id", "kind", "amount", "ride", "payout", "note", "created_at"]


class EarningsDaySerializer(serializers.Serializer):
    date = serializers.DateField()
    trips = serializers.IntegerField()
    gross_amount = serializers.IntegerField()


class EarningsSummarySerializer(serializers.Serializer):
    period = serializers.ChoiceField(choices=["day", "week", "month"])
    starts_at = serializers.DateTimeField()
    trips = serializers.IntegerField()
    fares_amount = serializers.IntegerField()
    tips_amount = serializers.IntegerField()
    incentives_amount = serializers.IntegerField()
    cancellation_fees_amount = serializers.IntegerField()
    commission_amount = serializers.IntegerField()
    net_amount = serializers.IntegerField(help_text="What you earned in the period.")
    cash_collected_amount = serializers.IntegerField(help_text="Cash you already hold from customers.")
    balance_amount = serializers.IntegerField(help_text="What Chicano Cruise owes you now (negative = you owe commission on cash trips).")
    by_day = EarningsDaySerializer(many=True)


class PayoutSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payout
        fields = ["id", "amount", "fee_amount", "method", "status", "bank_name", "bank_account_number", "bank_account_name",
                  "reference", "failure_reason", "paid_at", "created_at"]


class IncentiveProgressSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    description = serializers.CharField()
    target_trips = serializers.IntegerField()
    completed_trips = serializers.IntegerField()
    reward_amount = serializers.IntegerField()
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    achieved = serializers.BooleanField()
    status = serializers.ChoiceField(choices=["active", "achieved", "ended"])


class IncentiveAdminSerializer(serializers.ModelSerializer):
    class Meta:
        model = Incentive
        fields = ["id", "service", "name", "description", "target_trips", "reward_amount", "starts_at", "ends_at",
                  "min_acceptance_rate", "is_active"]
