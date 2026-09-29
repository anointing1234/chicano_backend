"""
Money. Every amount is an integer in KOBO (₦1 = 100 kobo), never a float.

Customer side
    Wallet / WalletTransaction   prepaid balance, refunds and credits land here. May go negative
                                 when a cash customer owes a cancellation fee (settled on next top-up).
    PaymentMethod                saved cards (only gateway tokens + last4 are stored, never card numbers)
    Payment                      every charge/refund attempt against a card or wallet (audit trail)
    PromoCode / PromoRedemption  discounts; the platform funds them, providers are paid the full fare

Provider side (drivers + riders)
    ProviderLedgerEntry          double-entry style ledger. Balance = sum(amount).
                                 Positive balance = Chicano Cruise owes the provider.
                                 Cash trips create a negative "cash_collected" entry because the
                                 provider already holds the customer's cash.
    Payout                       money sent to the provider's bank (weekly batch or instant cash-out)
    Incentive / IncentiveAward   trip-count bonuses (e.g. "25 trips this weekend = ₦10,000")

Worked example (cash car trip, from the design):
    fare ₦7,850 · promo -₦785 · tip ₦500 · commission 15% of fare = ₦1,177.50
    ledger (kobo): +785,000 fare, -117,750 commission, +50,000 tip, -756,500 cash_collected  => -39,250
    i.e. the driver keeps ₦7,565 cash and owes ₦392.50 net, settled against the next payout.
"""
from django.conf import settings
from django.db import models

from apps.core.models import BaseModel, Service


class Wallet(BaseModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wallet")
    balance_amount = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=3, default="NGN")


class WalletTxKind(models.TextChoices):
    TOPUP = "topup", "Top-up"
    RIDE_PAYMENT = "ride_payment", "Ride payment"
    TIP = "tip", "Tip"
    REFUND = "refund", "Refund"
    CREDIT = "credit", "Ride credit / referral"
    CANCELLATION_FEE = "cancellation_fee", "Cancellation fee"
    ADJUSTMENT = "adjustment", "Adjustment"


class WalletTransaction(BaseModel):
    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="transactions")
    kind = models.CharField(max_length=20, choices=WalletTxKind.choices)
    amount = models.BigIntegerField(help_text="Signed: + credit, - debit (kobo).")
    balance_after = models.BigIntegerField()
    ride = models.ForeignKey("rides.Ride", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reference = models.CharField(max_length=80, blank=True)
    note = models.CharField(max_length=255, blank=True)


class PaymentMethod(BaseModel):
    """A saved card. Card numbers never touch our servers: the app tokenises with the gateway SDK."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payment_methods")
    brand = models.CharField(max_length=20)            # visa, mastercard, verve
    last4 = models.CharField(max_length=4)
    exp_month = models.PositiveSmallIntegerField()
    exp_year = models.PositiveSmallIntegerField()
    gateway = models.CharField(max_length=20, default="dummy")
    gateway_token = models.CharField(max_length=255, help_text="Authorization/token from Paystack or similar.")
    is_default = models.BooleanField(default=False)


class PaymentPurpose(models.TextChoices):
    RIDE = "ride", "Ride fare"
    TIP = "tip", "Tip"
    CANCELLATION_FEE = "cancellation_fee", "Cancellation fee"
    TOPUP = "topup", "Wallet top-up"
    REFUND = "refund", "Refund"


class Payment(BaseModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payments")
    ride = models.ForeignKey("rides.Ride", null=True, blank=True, on_delete=models.SET_NULL, related_name="payments")
    purpose = models.CharField(max_length=20, choices=PaymentPurpose.choices)
    method = models.CharField(max_length=6)            # cash | card | wallet
    amount = models.BigIntegerField()
    status = models.CharField(max_length=10, choices=[("succeeded", "Succeeded"), ("failed", "Failed")])
    gateway_reference = models.CharField(max_length=120, blank=True)
    failure_reason = models.CharField(max_length=255, blank=True)


class DiscountType(models.TextChoices):
    PERCENT = "percent", "Percent"
    FLAT = "flat", "Flat amount"


class PromoCode(BaseModel):
    code = models.CharField(max_length=30, unique=True)
    description = models.CharField(max_length=160, blank=True, help_text="What the user sees, e.g. '10% off your next 2 rides'.")
    discount_type = models.CharField(max_length=7, choices=DiscountType.choices)
    value = models.PositiveIntegerField(help_text="Percent (e.g. 10) or flat kobo amount.")
    max_discount_amount = models.PositiveIntegerField(null=True, blank=True, help_text="Cap for percent promos (kobo).")
    service = models.CharField(max_length=4, choices=Service.choices, null=True, blank=True, help_text="Empty = cars and bikes.")
    usage_limit = models.PositiveIntegerField(null=True, blank=True, help_text="Total redemptions allowed.")
    per_user_limit = models.PositiveIntegerField(default=1)
    first_ride_only = models.BooleanField(default=False)
    budget_amount = models.PositiveBigIntegerField(null=True, blank=True, help_text="Stops when discounts reach this (kobo).")
    spent_amount = models.PositiveBigIntegerField(default=0)
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField()
    is_active = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        self.code = self.code.upper().strip()
        super().save(*args, **kwargs)


class PromoRedemption(BaseModel):
    promo = models.ForeignKey(PromoCode, on_delete=models.PROTECT, related_name="redemptions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="promo_redemptions")
    ride = models.OneToOneField("rides.Ride", on_delete=models.CASCADE, related_name="promo_redemption")
    discount_amount = models.PositiveIntegerField()


class LedgerKind(models.TextChoices):
    FARE = "fare", "Trip fare"
    COMMISSION = "commission", "Platform commission"
    TIP = "tip", "Tip"
    CASH_COLLECTED = "cash_collected", "Cash collected from customer"
    CANCELLATION_FEE = "cancellation_fee", "Cancellation fee (your share)"
    INCENTIVE = "incentive", "Incentive bonus"
    PAYOUT = "payout", "Payout to bank"
    PAYOUT_FEE = "payout_fee", "Instant payout fee"
    PAYOUT_REVERSAL = "payout_reversal", "Failed payout returned"
    ADJUSTMENT = "adjustment", "Adjustment by staff"


class ProviderLedgerEntry(BaseModel):
    provider = models.ForeignKey("providers.ProviderProfile", on_delete=models.PROTECT, related_name="ledger")
    ride = models.ForeignKey("rides.Ride", null=True, blank=True, on_delete=models.SET_NULL, related_name="ledger_entries")
    payout = models.ForeignKey("payments.Payout", null=True, blank=True, on_delete=models.SET_NULL, related_name="ledger_entries")
    kind = models.CharField(max_length=20, choices=LedgerKind.choices, db_index=True)
    amount = models.BigIntegerField(help_text="Signed kobo. + owed to provider, - owed by provider.")
    note = models.CharField(max_length=255, blank=True)


class PayoutStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    PAID = "paid", "Paid"
    FAILED = "failed", "Failed"
    ON_HOLD = "on_hold", "On hold"


class Payout(BaseModel):
    provider = models.ForeignKey("providers.ProviderProfile", on_delete=models.PROTECT, related_name="payouts")
    amount = models.PositiveBigIntegerField()
    fee_amount = models.PositiveIntegerField(default=0)
    method = models.CharField(max_length=10, choices=[("scheduled", "Weekly"), ("instant", "Instant")])
    status = models.CharField(max_length=10, choices=PayoutStatus.choices, default=PayoutStatus.PENDING, db_index=True)
    bank_name = models.CharField(max_length=80)
    bank_account_number = models.CharField(max_length=20)
    bank_account_name = models.CharField(max_length=120)
    reference = models.CharField(max_length=80, blank=True)
    failure_reason = models.CharField(max_length=255, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    processed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")


class Incentive(BaseModel):
    service = models.CharField(max_length=4, choices=Service.choices)
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=160, blank=True)
    target_trips = models.PositiveIntegerField()
    reward_amount = models.PositiveIntegerField(help_text="kobo")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    min_acceptance_rate = models.PositiveSmallIntegerField(default=80)
    is_active = models.BooleanField(default=True)


class IncentiveAward(BaseModel):
    incentive = models.ForeignKey(Incentive, on_delete=models.PROTECT, related_name="awards")
    provider = models.ForeignKey("providers.ProviderProfile", on_delete=models.CASCADE, related_name="incentive_awards")
    amount = models.PositiveIntegerField()

    class Meta(BaseModel.Meta):
        constraints = [models.UniqueConstraint(fields=["incentive", "provider"], name="one_award_per_incentive")]
