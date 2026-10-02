"""
Rides: the trip itself, its stops, dispatch offers, an event timeline and ratings.
One `Ride` table for both services; `service` says car or bike.
Cars carry passengers. Bikes are the dispatch service: they carry packages (see `package_*` and `recipient_*`).

Status machine (enforced in apps/rides/services.py, never set `status` directly):

    scheduled ──(15 min before)──┐
                                 ▼
    searching ──accept──► accepted ──arrive──► arrived ──start──► in_progress ──complete──► completed
        │                    │                   │   (bike: package collected first)
        ├──► no_provider     └──────► cancelled ◄┘   (customer, provider, staff or no-show)

What the apps poll:
    customer: GET /rides/{id}/                 every 3-5 s while the ride is active
    provider: GET /provider/offers/current/    every 3 s while online and free
              GET /provider/trips/current/     every 5 s during a trip
"""
import secrets

from django.conf import settings
from django.db import models

from apps.core.models import BaseModel, Service

from .delivery import PackageKind, PackageSize


class RideStatus(models.TextChoices):
    SCHEDULED = "scheduled", "Scheduled"
    SEARCHING = "searching", "Finding a driver/rider"
    ACCEPTED = "accepted", "Driver/rider on the way"
    ARRIVED = "arrived", "Driver/rider at pickup"
    IN_PROGRESS = "in_progress", "On trip"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"
    NO_PROVIDER = "no_provider", "No driver/rider found"


# Rides that block the customer from booking again and the provider from taking offers.
ACTIVE_STATUSES = [RideStatus.SEARCHING, RideStatus.ACCEPTED, RideStatus.ARRIVED, RideStatus.IN_PROGRESS]
PROVIDER_BUSY_STATUSES = [RideStatus.ACCEPTED, RideStatus.ARRIVED, RideStatus.IN_PROGRESS]


class PaymentMethodKind(models.TextChoices):
    CASH = "cash", "Cash"
    CARD = "card", "Card"
    WALLET = "wallet", "Chicano wallet"


class PaymentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PAID = "paid", "Paid"
    FAILED = "failed", "Failed"
    REFUNDED = "refunded", "Refunded"
    PARTIALLY_REFUNDED = "partially_refunded", "Partially refunded"


class CancelledBy(models.TextChoices):
    CUSTOMER = "customer", "Customer"
    PROVIDER = "provider", "Driver/rider"
    STAFF = "staff", "Staff"
    SYSTEM = "system", "System"


def _share_token() -> str:
    return secrets.token_urlsafe(12)


class Ride(BaseModel):
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="rides")
    provider = models.ForeignKey("providers.ProviderProfile", null=True, blank=True, on_delete=models.PROTECT, related_name="rides")
    vehicle = models.ForeignKey("providers.Vehicle", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    service = models.CharField(max_length=4, choices=Service.choices, db_index=True)
    ride_type = models.ForeignKey("pricing.RideType", on_delete=models.PROTECT)
    quote = models.OneToOneField("pricing.FareQuote", null=True, blank=True, on_delete=models.SET_NULL, related_name="ride")
    status = models.CharField(max_length=12, choices=RideStatus.choices, db_index=True)
    city = models.CharField(max_length=60, default="Lagos")

    # Places (addresses are what the customer saw; lat/lng are what drivers navigate to)
    pickup_lat = models.DecimalField(max_digits=9, decimal_places=6)
    pickup_lng = models.DecimalField(max_digits=9, decimal_places=6)
    pickup_address = models.CharField(max_length=255)
    pickup_note = models.CharField(max_length=255, blank=True, help_text="Landmark note for the driver/rider, e.g. 'Blue gate, opposite GTBank'.")
    dropoff_lat = models.DecimalField(max_digits=9, decimal_places=6)
    dropoff_lng = models.DecimalField(max_digits=9, decimal_places=6)
    dropoff_address = models.CharField(max_length=255)
    scheduled_for = models.DateTimeField(null=True, blank=True)

    # Money (kobo). `total` = what the customer pays for the trip (before tip).
    distance_m = models.PositiveIntegerField(default=0)
    duration_s = models.PositiveIntegerField(default=0)
    gross_amount = models.PositiveIntegerField(default=0, help_text="Fare before discount, incl. waiting charge.")
    discount_amount = models.PositiveIntegerField(default=0)
    wait_charge_amount = models.PositiveIntegerField(default=0)
    total_amount = models.PositiveIntegerField(default=0)
    tip_amount = models.PositiveIntegerField(default=0)
    commission_amount = models.PositiveIntegerField(default=0)
    cancellation_fee_amount = models.PositiveIntegerField(default=0)
    refunded_amount = models.PositiveIntegerField(default=0)
    payment_method = models.CharField(max_length=6, choices=PaymentMethodKind.choices, default=PaymentMethodKind.CASH)
    card = models.ForeignKey("payments.PaymentMethod", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    cash_collected_at = models.DateTimeField(null=True, blank=True)

    # Timeline
    requested_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    arrived_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.CharField(max_length=10, choices=CancelledBy.choices, blank=True)
    cancel_reason = models.CharField(max_length=255, blank=True)

    # Dispatch: the package and who receives it (bikes only; blank for cars)
    package_kind = models.CharField(max_length=20, choices=PackageKind.choices, blank=True)
    package_size = models.CharField(max_length=10, choices=PackageSize.choices, blank=True)
    package_contents = models.CharField(max_length=120, blank=True, help_text="Optional: what exactly is inside.")
    package_fragile = models.BooleanField(default=False)
    recipient_name = models.CharField(max_length=80, blank=True)
    recipient_phone = models.CharField(max_length=20, blank=True, help_text="E.164, e.g. +2348034125567.")
    package_collected_at = models.DateTimeField(null=True, blank=True, help_text="Rider confirmed the package at pickup.")

    # Old passenger-bike fields, kept for past trips. No longer used.
    helmet_handed_over_at = models.DateTimeField(null=True, blank=True)
    helmet_returned_at = models.DateTimeField(null=True, blank=True)

    # Dispatch + sharing
    needs_manual_dispatch = models.BooleanField(default=False, db_index=True, help_text="Shown in Super Admin > Dispatch.")
    share_token = models.CharField(max_length=32, unique=True, default=_share_token)

    class Meta(BaseModel.Meta):
        indexes = [models.Index(fields=["status", "service"]), models.Index(fields=["customer", "-created_at"])]

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @property
    def cash_due_amount(self) -> int:
        """Cash the provider should collect: trip total + tip (0 for card/wallet rides)."""
        return self.total_amount + self.tip_amount if self.payment_method == PaymentMethodKind.CASH else 0

    def __str__(self):
        return f"Ride {str(self.id)[:8]} {self.service} {self.status}"


class RideStop(BaseModel):
    ride = models.ForeignKey(Ride, on_delete=models.CASCADE, related_name="stops")
    order = models.PositiveSmallIntegerField()
    lat = models.DecimalField(max_digits=9, decimal_places=6)
    lng = models.DecimalField(max_digits=9, decimal_places=6)
    address = models.CharField(max_length=255)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ["order"]


class OfferStatus(models.TextChoices):
    SENT = "sent", "Sent"
    ACCEPTED = "accepted", "Accepted"
    DECLINED = "declined", "Declined"
    EXPIRED = "expired", "Expired (no answer)"
    WITHDRAWN = "withdrawn", "Withdrawn (ride cancelled/reassigned)"


class RideOffer(BaseModel):
    """A request sent to ONE provider, who has DISPATCH_OFFER_SECONDS to accept."""
    ride = models.ForeignKey(Ride, on_delete=models.CASCADE, related_name="offers")
    provider = models.ForeignKey("providers.ProviderProfile", on_delete=models.CASCADE, related_name="offers")
    status = models.CharField(max_length=10, choices=OfferStatus.choices, default=OfferStatus.SENT, db_index=True)
    distance_to_pickup_m = models.PositiveIntegerField()
    eta_to_pickup_s = models.PositiveIntegerField()
    expires_at = models.DateTimeField()
    responded_at = models.DateTimeField(null=True, blank=True)
    is_manual = models.BooleanField(default=False, help_text="Assigned by staff from the Dispatch screen.")


class RideEvent(BaseModel):
    """Immutable timeline (shown in Super Admin trip detail and used for disputes)."""
    ride = models.ForeignKey(Ride, on_delete=models.CASCADE, related_name="events")
    event = models.CharField(max_length=40)          # e.g. "requested", "offer_sent", "accepted", "cancelled"
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    data = models.JSONField(default=dict, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ["created_at"]


class RatingDirection(models.TextChoices):
    CUSTOMER_TO_PROVIDER = "customer_to_provider", "Customer rates driver/rider"
    PROVIDER_TO_CUSTOMER = "provider_to_customer", "Driver/rider rates customer"


class Rating(BaseModel):
    ride = models.ForeignKey(Ride, on_delete=models.CASCADE, related_name="ratings")
    direction = models.CharField(max_length=24, choices=RatingDirection.choices)
    stars = models.PositiveSmallIntegerField()
    tags = models.JSONField(default=list, blank=True, help_text='e.g. ["Smooth driving", "Package arrived safe"]')
    comment = models.CharField(max_length=500, blank=True)

    class Meta(BaseModel.Meta):
        constraints = [models.UniqueConstraint(fields=["ride", "direction"], name="one_rating_per_direction")]
