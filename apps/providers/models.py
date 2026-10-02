"""
Providers = the people who do the trips.

    ProviderProfile.service = "car"  -> a DRIVER (Driver app · Cars)
    ProviderProfile.service = "bike" -> a dispatch RIDER  (Rider app · Bikes: package delivery)

Same table, same onboarding pipeline, same earnings ledger. Differences are driven by
`service`: required documents, vehicle kind, and the bike kit (helmet, delivery box).

Onboarding pipeline (status):
    applied -> documents_pending -> under_review -> approved
                                         └────────-> rejected
    approved <-> suspended (by staff)
"""
from django.conf import settings
from django.db import models

from apps.core.models import BaseModel, Service


class ProviderStatus(models.TextChoices):
    APPLIED = "applied", "Applied"
    DOCUMENTS_PENDING = "documents_pending", "Documents pending"
    UNDER_REVIEW = "under_review", "Under review"
    APPROVED = "approved", "Approved"
    SUSPENDED = "suspended", "Suspended"
    REJECTED = "rejected", "Rejected"


class ProviderProfile(BaseModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="provider_profile")
    service = models.CharField(max_length=4, choices=Service.choices, db_index=True, help_text="car = driver, bike = rider")
    status = models.CharField(max_length=20, choices=ProviderStatus.choices, default=ProviderStatus.APPLIED, db_index=True)
    status_reason = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=60, default="Lagos")
    date_of_birth = models.DateField(null=True, blank=True)

    # Availability + live location (updated by POST /provider/location/ every ~5 s while online)
    is_online = models.BooleanField(default=False, db_index=True)
    last_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    last_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    last_heading = models.PositiveSmallIntegerField(null=True, blank=True)
    last_location_at = models.DateTimeField(null=True, blank=True)

    # Performance (recomputed by apps.rides.services after each offer/trip)
    rating_avg = models.DecimalField(max_digits=3, decimal_places=2, default=5.00)
    rating_count = models.PositiveIntegerField(default=0)
    total_trips = models.PositiveIntegerField(default=0)
    offers_received = models.PositiveIntegerField(default=0)
    offers_accepted = models.PositiveIntegerField(default=0)
    trips_cancelled = models.PositiveIntegerField(default=0)

    # Payouts
    bank_name = models.CharField(max_length=80, blank=True)
    bank_account_number = models.CharField(max_length=20, blank=True)
    bank_account_name = models.CharField(max_length=120, blank=True)

    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    @property
    def kind(self) -> str:
        """"driver" or "rider": use this word in UI copy."""
        return "driver" if self.service == Service.CAR else "rider"

    @property
    def acceptance_rate(self) -> float:
        return round(100 * self.offers_accepted / self.offers_received, 1) if self.offers_received else 100.0

    @property
    def cancellation_rate(self) -> float:
        done = self.total_trips + self.trips_cancelled
        return round(100 * self.trips_cancelled / done, 1) if done else 0.0

    def __str__(self):
        return f"{self.kind.title()} {self.user}"


class VehicleStatus(models.TextChoices):
    PENDING = "pending", "Pending inspection"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    INACTIVE = "inactive", "Inactive"


class Vehicle(BaseModel):
    """A car (drivers) or a motorbike (riders). A provider can have several; one is active."""
    provider = models.ForeignKey(ProviderProfile, on_delete=models.CASCADE, related_name="vehicles")
    kind = models.CharField(max_length=4, choices=Service.choices, help_text="car or bike (matches the provider's service)")
    make = models.CharField(max_length=40)
    model = models.CharField(max_length=40)
    year = models.PositiveSmallIntegerField()
    color = models.CharField(max_length=30)
    plate_number = models.CharField(max_length=15, unique=True)
    seats = models.PositiveSmallIntegerField(default=4)
    ride_types = models.ManyToManyField("pricing.RideType", blank=True, help_text="Ride types this vehicle may serve (set on approval).")
    # Bike kit (ignored for cars). Riders need their own helmet to go online.
    has_rider_helmet = models.BooleanField(default=False)
    has_delivery_box = models.BooleanField(default=False, help_text="Top box or delivery bag for packages.")
    has_reflective_vest = models.BooleanField(default=False)
    has_passenger_helmet = models.BooleanField(default=False, help_text="Old passenger-bike field. No longer required.")
    status = models.CharField(max_length=10, choices=VehicleStatus.choices, default=VehicleStatus.PENDING)
    is_active = models.BooleanField(default=True, help_text="The vehicle currently being driven.")

    def __str__(self):
        return f"{self.color} {self.make} {self.model} ({self.plate_number})"


class DocumentType(models.TextChoices):
    DRIVERS_LICENCE = "drivers_licence", "Driver's licence (cars)"
    RIDERS_LICENCE = "riders_licence", "Rider's licence, class A (bikes)"
    INSURANCE = "insurance", "Insurance certificate"
    VEHICLE_REGISTRATION = "vehicle_registration", "Vehicle / bike registration"
    NATIONAL_ID = "national_id", "National ID (NIN)"
    PROFILE_PHOTO = "profile_photo", "Profile photo"


# Documents a provider must have APPROVED before their account can be approved.
REQUIRED_DOCUMENTS = {
    Service.CAR: [DocumentType.DRIVERS_LICENCE, DocumentType.INSURANCE, DocumentType.VEHICLE_REGISTRATION],
    Service.BIKE: [DocumentType.RIDERS_LICENCE, DocumentType.INSURANCE, DocumentType.VEHICLE_REGISTRATION],
}


class DocumentStatus(models.TextChoices):
    PENDING = "pending", "Pending review"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    EXPIRED = "expired", "Expired"


class ProviderDocument(BaseModel):
    provider = models.ForeignKey(ProviderProfile, on_delete=models.CASCADE, related_name="documents")
    doc_type = models.CharField(max_length=30, choices=DocumentType.choices)
    file = models.FileField(upload_to="provider_documents/%Y/%m/")
    back_file = models.FileField(upload_to="provider_documents/%Y/%m/", null=True, blank=True, help_text="Back side, if any.")
    number = models.CharField(max_length=60, blank=True)
    expires_at = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=DocumentStatus.choices, default=DocumentStatus.PENDING, db_index=True)
    rejection_reason = models.CharField(max_length=255, blank=True, help_text="Shown to the driver/rider in the app.")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
