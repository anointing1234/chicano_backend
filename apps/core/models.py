"""
Shared building blocks for every Chicano Cruise model.

* `BaseModel`  gives every table a UUID primary key plus created/updated timestamps.
  UUIDs (not 1, 2, 3...) are safe to expose in URLs and mobile apps: nobody can guess
  the next ride or user ID.
* `Service`    the two services on one platform. Everything that differs between cars
  and bikes (ride types, providers, vehicles, rides, promos) carries this field.
* `AuditLog`   an append-only record of every staff action (approve, refund, ban...).
"""
import uuid

from django.conf import settings
from django.db import models


class Service(models.TextChoices):
    """The two services. Drivers serve CAR rides; riders serve BIKE rides."""
    CAR = "car", "Car"
    BIKE = "bike", "Bike"


class BaseModel(models.Model):
    """Abstract base: UUID id + created_at + updated_at on every table."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]


class AuditLog(BaseModel):
    """
    Who did what, to which record, from the Super Admin.

    Written by `apps.core.audit.log_action()`; never edited or deleted.
    """
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="audit_entries")
    action = models.CharField(max_length=64)                 # e.g. "provider.approve", "ride.refund"
    target_type = models.CharField(max_length=64)            # e.g. "ProviderProfile"
    target_id = models.CharField(max_length=64)
    data = models.JSONField(default=dict, blank=True)        # before/after values, reason, amounts
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        indexes = [models.Index(fields=["target_type", "target_id"])]

    def __str__(self):
        return f"{self.action} {self.target_type}:{self.target_id}"
