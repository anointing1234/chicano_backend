"""
Customers: people who book rides in the User app · Cars and the User app · Bikes.

Both user apps share ONE customer profile, wallet, saved places and ride history.
The `service` on each ride says whether it was a car or a bike trip.
"""
import secrets

from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


def _referral_code() -> str:
    return "CC" + secrets.token_hex(3).upper()   # e.g. CC4F2A91


class CustomerProfile(BaseModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="customer_profile")
    rating_avg = models.DecimalField(max_digits=3, decimal_places=2, default=5.00, help_text="Average rating given by drivers/riders.")
    rating_count = models.PositiveIntegerField(default=0)
    total_trips = models.PositiveIntegerField(default=0)
    referral_code = models.CharField(max_length=12, unique=True, default=_referral_code)
    referred_by = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="referrals")
    data_saver = models.BooleanField(default=False, help_text="Lite map + fewer images (weak networks).")

    def __str__(self):
        return f"Customer {self.user}"


class PlaceLabel(models.TextChoices):
    HOME = "home", "Home"
    WORK = "work", "Work"
    OTHER = "other", "Other"


class SavedPlace(BaseModel):
    customer = models.ForeignKey(CustomerProfile, on_delete=models.CASCADE, related_name="saved_places")
    label = models.CharField(max_length=10, choices=PlaceLabel.choices, default=PlaceLabel.OTHER)
    name = models.CharField(max_length=120, help_text='Display name, e.g. "Home" or "Mum\'s house".')
    address = models.CharField(max_length=255)
    lat = models.DecimalField(max_digits=9, decimal_places=6)
    lng = models.DecimalField(max_digits=9, decimal_places=6)
    place_id = models.CharField(max_length=255, blank=True, help_text="Google Places ID, if known.")
    note = models.CharField(max_length=255, blank=True, help_text="Default pickup note, e.g. 'Blue gate, opposite GTBank'.")


class EmergencyContact(BaseModel):
    """People alerted on SOS and offered for trip sharing."""
    customer = models.ForeignKey(CustomerProfile, on_delete=models.CASCADE, related_name="emergency_contacts")
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20)
    relationship = models.CharField(max_length=60, blank=True)
