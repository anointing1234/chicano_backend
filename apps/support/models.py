"""
Support & safety: in-app notifications, help tickets, lost items and SOS alerts.
Used by all four apps (customers and providers) and worked by staff in the Super Admin.
"""
from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


class Notification(BaseModel):
    """Inbox item. Also pushed to the user's devices when created via `support.services.notify`."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    title = models.CharField(max_length=120)
    body = models.CharField(max_length=255)
    data = models.JSONField(default=dict, blank=True, help_text='Deep-link payload, e.g. {"type": "ride_update", "ride_id": "..."}')
    read_at = models.DateTimeField(null=True, blank=True)


class TicketStatus(models.TextChoices):
    OPEN = "open", "Open"
    PENDING = "pending", "Waiting for you"
    RESOLVED = "resolved", "Resolved"
    CLOSED = "closed", "Closed"


class TicketCategory(models.TextChoices):
    FARE = "fare", "Fares & payments"
    DRIVER = "driver", "Driver / rider behaviour"
    SAFETY = "safety", "Safety"
    LOST_ITEM = "lost_item", "Lost item"
    ACCOUNT = "account", "Account"
    PAYOUT = "payout", "Payouts (drivers/riders)"
    DOCUMENTS = "documents", "Documents (drivers/riders)"
    OTHER = "other", "Other"


class SupportTicket(BaseModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tickets")
    ride = models.ForeignKey("rides.Ride", null=True, blank=True, on_delete=models.SET_NULL, related_name="tickets")
    category = models.CharField(max_length=12, choices=TicketCategory.choices)
    subject = models.CharField(max_length=160)
    status = models.CharField(max_length=10, choices=TicketStatus.choices, default=TicketStatus.OPEN, db_index=True)
    priority = models.CharField(max_length=6, choices=[("low", "Low"), ("normal", "Normal"), ("high", "High"), ("urgent", "Urgent")], default="normal")
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_tickets")


class TicketMessage(BaseModel):
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    body = models.TextField()
    attachment = models.FileField(upload_to="ticket_attachments/%Y/%m/", null=True, blank=True)
    is_internal_note = models.BooleanField(default=False, help_text="Staff-only note, never shown in the apps.")

    class Meta(BaseModel.Meta):
        ordering = ["created_at"]


class LostItemStatus(models.TextChoices):
    REPORTED = "reported", "Reported"
    PROVIDER_CONTACTED = "provider_contacted", "Driver/rider contacted"
    FOUND = "found", "Found"
    RETURNED = "returned", "Returned"
    NOT_FOUND = "not_found", "Not found"


class LostItemReport(BaseModel):
    ride = models.ForeignKey("rides.Ride", on_delete=models.CASCADE, related_name="lost_items")
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="lost_items")
    category = models.CharField(max_length=20, choices=[("phone", "Phone"), ("bag", "Bag"), ("wallet_id", "Wallet / ID"), ("other", "Other")])
    description = models.CharField(max_length=255)
    contact_phone = models.CharField(max_length=20, help_text="Where we can reach the customer (maybe not the lost phone).")
    status = models.CharField(max_length=20, choices=LostItemStatus.choices, default=LostItemStatus.REPORTED)
    ticket = models.OneToOneField(SupportTicket, null=True, blank=True, on_delete=models.SET_NULL, related_name="lost_item")


class SOSStatus(models.TextChoices):
    OPEN = "open", "Open"
    ACKNOWLEDGED = "acknowledged", "Safety team on it"
    RESOLVED = "resolved", "Resolved"


class SOSAlert(BaseModel):
    """Raised from the SOS button (customer or provider). Staff see these at the top of the Super Admin."""
    ride = models.ForeignKey("rides.Ride", null=True, blank=True, on_delete=models.SET_NULL, related_name="sos_alerts")
    raised_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sos_alerts")
    lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    status = models.CharField(max_length=12, choices=SOSStatus.choices, default=SOSStatus.OPEN, db_index=True)
    contacts_notified = models.JSONField(default=list, blank=True)
    handled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    notes = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
