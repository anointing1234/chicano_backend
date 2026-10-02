"""
Notifications (in-app inbox + push) and safety actions.

Push: the Expo apps register an Expo push token via POST /auth/devices/.
`send_push` uses the console in dev. For production set PUSH_BACKEND=expo and send to
https://exp.host/--/api/v2/push/send (Expo push service; works for iOS + Android with
no FCM/APNs code in the app), or PUSH_BACKEND=fcm if you use bare FCM tokens.
"""
import logging

from django.conf import settings
from django.utils import timezone

from .models import Notification, SOSAlert

log = logging.getLogger(__name__)


def send_push(user, title: str, body: str, data: dict) -> None:
    tokens = list(user.devices.values_list("push_token", flat=True))
    if not tokens:
        return
    if settings.PUSH_BACKEND == "console":
        log.info("[PUSH to %s x%d] %s | %s | %s", user.phone, len(tokens), title, body, data)
        return
    if settings.PUSH_BACKEND == "expo":
        _send_expo_push(tokens, title, body, data)
        return
    raise NotImplementedError("PUSH_BACKEND=fcm: implement FCM HTTP v1 here (or use expo).")


EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


def _send_expo_push(tokens: list[str], title: str, body: str, data: dict) -> None:
    """
    Send through Expo's push service (tokens look like ExponentPushToken[xxxx]).
    `data` arrives in the app's notification response listener, e.g. {"type": "ride_offer", "offer_id": "..."}.
    Tokens Expo reports as DeviceNotRegistered are deleted so we stop sending to uninstalled apps.
    """
    import requests
    from apps.accounts.models import Device
    messages = [{"to": t, "title": title, "body": body, "data": data, "sound": "default",
                 "priority": "high", "channelId": "default"} for t in tokens]
    resp = requests.post(EXPO_PUSH_URL, json=messages, timeout=5,
                         headers={"Accept": "application/json", "Content-Type": "application/json"})
    resp.raise_for_status()
    for token, ticket in zip(tokens, resp.json().get("data", [])):
        if ticket.get("status") == "error" and ticket.get("details", {}).get("error") == "DeviceNotRegistered":
            Device.objects.filter(push_token=token).delete()


def notify(user, title: str, body: str, data: dict | None = None) -> Notification:
    """Create an inbox notification and push it to the user's phones."""
    note = Notification.objects.create(user=user, title=title, body=body, data=data or {})
    try:
        send_push(user, title, body, data or {})
    except Exception:   # push must never break the business action that triggered it
        log.exception("push failed")
    return note


def raise_sos(user, ride=None, lat=None, lng=None) -> SOSAlert:
    """Alert the safety team and the customer's emergency contacts (by SMS, works without data on their side)."""
    # Save the alert FIRST so the safety team always sees it, even if texting the contacts fails.
    alert = SOSAlert.objects.create(ride=ride, raised_by=user, lat=lat, lng=lng, contacts_notified=[])
    contacts = []
    if hasattr(user, "customer_profile"):
        from apps.accounts.services import send_sms
        link = f"{settings.SHARE_BASE_URL}{ride.share_token}" if ride else ""
        for c in user.customer_profile.emergency_contacts.all():
            try:
                send_sms(c.phone, f"{user.first_name or 'Your contact'} pressed SOS on Chicano Cruise. Live trip: {link}")
                contacts.append({"name": c.name, "phone": c.phone})
            except Exception:   # an SMS outage must never block an SOS
                log.exception("SOS SMS to emergency contact failed")
                contacts.append({"name": c.name, "phone": c.phone, "failed": True})
        if contacts:
            alert.contacts_notified = contacts
            alert.save(update_fields=["contacts_notified", "updated_at"])
    if ride:
        from apps.rides.models import RideEvent
        RideEvent.objects.create(ride=ride, event="sos_raised", actor=user, data={"alert_id": str(alert.id)})
    log.warning("SOS raised by %s (ride %s)", user.phone, ride.id if ride else "-")
    return alert


def mark_all_read(user) -> int:
    return Notification.objects.filter(user=user, read_at__isnull=True).update(read_at=timezone.now())
