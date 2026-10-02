"""
Super Admin business actions, shared by BOTH staff front doors:

    * the web dashboard (apps/dashboard: Django templates, HTML forms)
    * the staff JSON API (apps/staff/views: /api/v1/staff/...)

Each function checks the rules, changes the data, notifies the person affected and writes
the audit log, so the two front doors can never drift apart. They raise ApiError/Conflict
on a rule violation; the dashboard turns those into a red message, the API into the usual
{"error": {...}} response.

`request` is passed through only for the audit log (who did it, from which IP).
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.accounts.models import UserStatus
from apps.core.audit import log_action
from apps.core.exceptions import ApiError, Conflict
from apps.core.roles import SUPPORT_REFUND_LIMIT
from apps.customers.models import CustomerProfile
from apps.payments.services import refund_ride
from apps.pricing.models import RideType
from apps.providers.models import (REQUIRED_DOCUMENTS, DocumentStatus, ProviderDocument, ProviderProfile, ProviderStatus,
                                   VehicleStatus)
from apps.rides.models import ACTIVE_STATUSES, PROVIDER_BUSY_STATUSES, Ride, RideStatus
from apps.support.models import SOSAlert, SOSStatus, SupportTicket, TicketMessage, TicketStatus
from apps.support.services import notify

User = get_user_model()


# =========================================================================== dashboard numbers
def overview(service: str | None) -> dict:
    """Numbers for the Overview page. `service` = None (all), "car" or "bike". Today = since midnight Lagos."""
    midnight = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    rides, providers = Ride.objects.all(), ProviderProfile.objects.all()
    docs, sos = ProviderDocument.objects.all(), SOSAlert.objects.all()
    if service:
        rides, providers = rides.filter(service=service), providers.filter(service=service)
        docs, sos = docs.filter(provider__service=service), sos.filter(ride__service=service)

    today_done = rides.filter(status=RideStatus.COMPLETED, completed_at__gte=midnight)
    money = today_done.aggregate(gross=Sum("total_amount"), commission=Sum("commission_amount"))

    # Last 7 days of completed trips (missing days filled with zeros).
    week_start = midnight - timedelta(days=6)
    rows = (rides.filter(status=RideStatus.COMPLETED, completed_at__gte=week_start)
            .annotate(day=TruncDate("completed_at")).values("day")
            .annotate(trips=Count("id"), gross=Sum("total_amount")))
    by_day = {r["day"]: r for r in rows}
    last_7 = []
    for i in range(7):
        d = (week_start + timedelta(days=i)).date()
        r = by_day.get(d, {})
        last_7.append({"date": d, "trips": r.get("trips", 0), "gross_amount": r.get("gross") or 0})

    return {
        "service": service or "all",
        "trips_today": today_done.count(),
        "cancelled_today": rides.filter(status=RideStatus.CANCELLED, cancelled_at__gte=midnight).count(),
        "active_trips": rides.filter(status__in=ACTIVE_STATUSES).count(),
        "gross_today_amount": money["gross"] or 0,
        "commission_today_amount": money["commission"] or 0,
        "online_providers": providers.filter(is_online=True).count(),
        "providers_awaiting_review": providers.filter(status=ProviderStatus.UNDER_REVIEW).count(),
        "documents_pending": docs.filter(status=DocumentStatus.PENDING).count(),
        "needs_manual_dispatch": rides.filter(status=RideStatus.SEARCHING, needs_manual_dispatch=True).count(),
        "open_sos": sos.exclude(status=SOSStatus.RESOLVED).count(),
        # Tickets and customers aren't split by service (one customer can use both User apps).
        "open_tickets": SupportTicket.objects.filter(status__in=["open", "pending"]).count(),
        "customers_total": CustomerProfile.objects.count(),
        "new_customers_today": CustomerProfile.objects.filter(created_at__gte=midnight).count(),
        "last_7_days": last_7,
    }


def live_providers(service: str | None) -> list[ProviderProfile]:
    """Online drivers/riders with a GPS fix from the last 10 minutes; `.on_trip` is set on each."""
    qs = (ProviderProfile.objects.select_related("user").prefetch_related("vehicles")
          .filter(is_online=True, last_lat__isnull=False, last_location_at__gte=timezone.now() - timedelta(minutes=10))
          .annotate(busy=Count("rides", filter=Q(rides__status__in=PROVIDER_BUSY_STATUSES)),
                    driving=Count("rides", filter=Q(rides__status="in_progress"))))
    if service:
        qs = qs.filter(service=service)
    pins = list(qs)
    for p in pins:
        p.on_trip = p.busy > 0
        p.phase = "trip" if p.driving else ("pickup" if p.busy else "available")   # map colours: ink / orange / green
    return pins


# =========================================================================== users
def set_user_status(request, user, new_status: str, reason: str = ""):
    """Suspend / ban / reinstate anyone in the combined users table."""
    if user.pk == request.user.pk:
        raise Conflict("cannot_change_self", "You can't change your own account status.")
    if user.is_staff and request.user.staff_role != "super_admin":
        raise ApiError("permission_denied", "Only a super admin can change another staff account.", status_code=403)
    if new_status != UserStatus.ACTIVE and not reason:
        raise ApiError("validation_error", "Give a reason.", details={"reason": ["This field is required."]})
    before = user.status
    user.status, user.status_reason = new_status, reason
    user.save(update_fields=["status", "status_reason", "updated_at"])
    # A suspended driver/rider must stop receiving trips immediately.
    if new_status != UserStatus.ACTIVE and hasattr(user, "provider_profile"):
        ProviderProfile.objects.filter(user=user).update(is_online=False)
    log_action(request, f"user.{new_status}", user, {"before": before, "reason": reason})
    return user


# =========================================================================== drivers & riders
def approve_provider(request, provider: ProviderProfile) -> ProviderProfile:
    """Needs every required document approved and an approved vehicle."""
    if provider.status == ProviderStatus.APPROVED:
        raise Conflict("invalid_state", "Already approved.", details={"status": provider.status})
    approved_docs = set(provider.documents.filter(status=DocumentStatus.APPROVED).values_list("doc_type", flat=True))
    missing = [d for d in REQUIRED_DOCUMENTS[provider.service] if d not in approved_docs]
    has_vehicle = provider.vehicles.filter(status=VehicleStatus.APPROVED).exists()
    if missing or not has_vehicle:
        labels = [ProviderDocument(doc_type=d).get_doc_type_display() for d in missing]
        parts = ([f"approve these documents: {', '.join(labels)}"] if missing else []) + ([] if has_vehicle else ["approve the vehicle"])
        raise Conflict("not_ready", "First " + " and ".join(parts) + ".",
                       details={"missing_documents": [str(m) for m in missing], "vehicle_approved": has_vehicle})
    provider.status, provider.status_reason = ProviderStatus.APPROVED, ""
    provider.approved_at, provider.approved_by = timezone.now(), request.user
    provider.save(update_fields=["status", "status_reason", "approved_at", "approved_by", "updated_at"])
    log_action(request, "provider.approve", provider)
    notify(provider.user, "You're approved!", "Welcome to Chicano Cruise. Go online to start earning.",
           data={"type": "provider_status", "status": "approved"})
    return provider


def reject_provider(request, provider: ProviderProfile, reason: str) -> ProviderProfile:
    if provider.status == ProviderStatus.APPROVED:
        raise Conflict("invalid_state", "Approved drivers/riders are suspended, not rejected.")
    provider.status, provider.status_reason = ProviderStatus.REJECTED, reason
    provider.save(update_fields=["status", "status_reason", "updated_at"])
    log_action(request, "provider.reject", provider, {"reason": reason})
    notify(provider.user, "Application update", reason, data={"type": "provider_status", "status": "rejected"})
    return provider


def suspend_provider(request, provider: ProviderProfile, reason: str) -> ProviderProfile:
    if provider.rides.filter(status__in=PROVIDER_BUSY_STATUSES).exists():
        raise Conflict("active_ride_exists", "They're on a trip. Wait for it to finish or cancel it first.")
    provider.status, provider.status_reason, provider.is_online = ProviderStatus.SUSPENDED, reason, False
    provider.save(update_fields=["status", "status_reason", "is_online", "updated_at"])
    log_action(request, "provider.suspend", provider, {"reason": reason})
    notify(provider.user, "Account on hold", reason, data={"type": "provider_status", "status": "suspended"})
    return provider


def reinstate_provider(request, provider: ProviderProfile) -> ProviderProfile:
    if provider.status not in (ProviderStatus.SUSPENDED, ProviderStatus.REJECTED):
        raise Conflict("invalid_state", "Only suspended or rejected accounts can be reinstated.", details={"status": provider.status})
    provider.status = ProviderStatus.APPROVED if provider.approved_at else ProviderStatus.UNDER_REVIEW
    provider.status_reason = ""
    provider.save(update_fields=["status", "status_reason", "updated_at"])
    log_action(request, "provider.reinstate", provider, {"status": provider.status})
    notify(provider.user, "You're back", "Your account is active again.", data={"type": "provider_status", "status": provider.status})
    return provider


def approve_vehicle(request, vehicle, codes: list[str]):
    """Set which ride types the vehicle may serve. Checks service, minimum year and (bikes) the rider's helmet."""
    types = list(RideType.objects.filter(code__in=codes, service=vehicle.kind, is_active=True))
    problems = {}
    if not codes:
        problems["ride_types"] = ["Choose at least one ride type."]
    unknown = sorted(set(codes) - {t.code for t in types})
    if unknown:
        problems["ride_types"] = [f"Unknown or wrong service: {', '.join(unknown)}"]
    too_old = [t.code for t in types if t.min_vehicle_year and vehicle.year < t.min_vehicle_year]
    if too_old:
        problems.setdefault("ride_types", []).append(f"Vehicle year {vehicle.year} is too old for: {', '.join(too_old)}")
    if vehicle.kind == "bike" and not vehicle.has_rider_helmet:
        problems["helmets"] = ["Dispatch bikes need a rider helmet."]
    if problems:
        first = next(iter(problems.values()))[0]
        raise ApiError("validation_error", first, details=problems)
    with transaction.atomic():
        vehicle.status = VehicleStatus.APPROVED
        vehicle.save(update_fields=["status", "updated_at"])
        vehicle.ride_types.set(types)
    log_action(request, "vehicle.approve", vehicle, {"ride_types": codes})
    return vehicle


def reject_vehicle(request, vehicle, reason: str):
    vehicle.status = VehicleStatus.REJECTED
    vehicle.save(update_fields=["status", "updated_at"])
    log_action(request, "vehicle.reject", vehicle, {"reason": reason})
    notify(vehicle.provider.user, "Vehicle not approved", reason,
           data={"type": "vehicle_status", "vehicle_id": str(vehicle.id), "status": "rejected"})
    return vehicle


def approve_document(request, doc: ProviderDocument) -> ProviderDocument:
    if doc.expires_at and doc.expires_at < timezone.localdate():
        raise Conflict("document_expired", "This document has expired. Reject it and ask for a new one.",
                       details={"expires_at": str(doc.expires_at)})
    doc.status, doc.rejection_reason = DocumentStatus.APPROVED, ""
    doc.reviewed_by, doc.reviewed_at = request.user, timezone.now()
    doc.save(update_fields=["status", "rejection_reason", "reviewed_by", "reviewed_at", "updated_at"])
    log_action(request, "document.approve", doc, {"doc_type": doc.doc_type})
    return doc


def reject_document(request, doc: ProviderDocument, reason: str) -> ProviderDocument:
    """The reason is shown to the driver/rider in their Documents screen."""
    doc.status, doc.rejection_reason = DocumentStatus.REJECTED, reason
    doc.reviewed_by, doc.reviewed_at = request.user, timezone.now()
    doc.save(update_fields=["status", "rejection_reason", "reviewed_by", "reviewed_at", "updated_at"])
    provider = doc.provider
    # An applicant goes back to "documents pending"; an approved provider keeps working until the doc expires.
    if provider.status == ProviderStatus.UNDER_REVIEW:
        provider.status = ProviderStatus.DOCUMENTS_PENDING
        provider.save(update_fields=["status", "updated_at"])
    log_action(request, "document.reject", doc, {"doc_type": doc.doc_type, "reason": reason})
    notify(provider.user, "Document needs attention", f"{doc.get_doc_type_display()}: {reason}",
           data={"type": "document_status", "document_id": str(doc.id), "status": "rejected"})
    return doc


# =========================================================================== trips
def refund(request, ride: Ride, amount: int, reason: str, claw_back: bool = False) -> Ride:
    """Card rides refund to the card; cash/wallet rides to the wallet. Support staff are capped at ₦5,000."""
    if request.user.staff_role == "support" and amount > SUPPORT_REFUND_LIMIT:
        raise ApiError("refund_limit", "Refunds over ₦5,000 need a finance team member.", status_code=403,
                       details={"limit_amount": SUPPORT_REFUND_LIMIT})
    if ride.status not in (RideStatus.COMPLETED, RideStatus.CANCELLED):
        raise Conflict("invalid_state", "Only completed or cancelled trips can be refunded.", details={"status": ride.status})
    refund_ride(ride, amount, reason, claw_back=claw_back)
    log_action(request, "ride.refund", ride, {"amount": amount, "reason": reason, "claw_back": claw_back})
    return ride


def cancel_ride(request, ride: Ride, reason: str) -> Ride:
    from apps.rides.services import cancel_by_staff
    ride = cancel_by_staff(ride, request.user, reason)
    log_action(request, "ride.cancel", ride, {"reason": reason})
    return ride


def assign_ride(request, ride: Ride, provider: ProviderProfile):
    """Manual dispatch: send this driver/rider a 60 s offer."""
    from apps.rides.services import manual_assign
    offer = manual_assign(ride, provider, request.user)
    log_action(request, "ride.manual_assign", ride, {"provider_id": str(provider.id)})
    return offer


# =========================================================================== support + safety
def reply_ticket(request, ticket: SupportTicket, body: str, internal: bool, status: str | None = None) -> SupportTicket:
    """Public replies push to the person and set the ticket to 'pending' (waiting on them) unless `status` is given."""
    TicketMessage.objects.create(ticket=ticket, sender=request.user, body=body, is_internal_note=internal)
    ticket.status = status or (ticket.status if internal else TicketStatus.PENDING)
    if ticket.assigned_to_id is None:
        ticket.assigned_to = request.user
    ticket.save(update_fields=["status", "assigned_to", "updated_at"])
    if not internal:
        notify(ticket.user, "Chicano Cruise support", body[:120], data={"type": "support_reply", "ticket_id": str(ticket.id)})
    log_action(request, "ticket.reply", ticket, {"internal": internal, "status": ticket.status})
    return ticket


BULK_TICKET_ACTIONS = {"assign_me": "Assigned to you", "resolve": "Marked resolved"}
BULK_MAX = 100


def bulk_update_tickets(request, ticket_ids: list, action: str) -> int:
    """Dashboard bulk action on the ticket queue: take ownership of, or resolve, up to 100 tickets at once."""
    if action not in BULK_TICKET_ACTIONS:
        raise ApiError("invalid_action", "Unknown bulk action.")
    tickets = list(SupportTicket.objects.filter(pk__in=ticket_ids[:BULK_MAX]))
    if not tickets:
        raise ApiError("nothing_selected", "Select at least one ticket.")
    for ticket in tickets:
        before = {"status": ticket.status, "assigned_to": str(ticket.assigned_to_id or "")}
        if action == "assign_me":
            ticket.assigned_to = request.user
        else:
            ticket.status = TicketStatus.RESOLVED
            if ticket.assigned_to_id is None:
                ticket.assigned_to = request.user
        ticket.save(update_fields=["status", "assigned_to", "updated_at"])
        log_action(request, "ticket.update", ticket, {"before": before, "status": ticket.status,
                                                       "assigned_to": str(ticket.assigned_to_id or ""), "bulk": True})
    return len(tickets)


def update_sos(request, alert: SOSAlert, to_status: str, notes: str = "") -> SOSAlert:
    """acknowledged: tell the person help is coming · resolved: close it with what happened."""
    if alert.status == SOSStatus.RESOLVED:
        raise Conflict("invalid_state", "This alert is already resolved.")
    alert.status, alert.handled_by = to_status, request.user
    if notes:
        stamp = timezone.localtime().strftime("%H:%M")
        alert.notes = f"{alert.notes}\n[{stamp} {request.user.full_name}] {notes}".strip()
    if to_status == SOSStatus.RESOLVED:
        alert.resolved_at = timezone.now()
    alert.save()
    if to_status == SOSStatus.ACKNOWLEDGED:
        notify(alert.raised_by, "Safety team is on it", "We've seen your alert and are contacting you now.",
               data={"type": "sos", "sos_id": str(alert.id)})
    log_action(request, f"sos.{to_status}", alert, {"notes": notes})
    return alert


# =========================================================================== payouts
def run_payouts(request) -> list:
    """Create this week's pending payouts (everyone owed ≥ ₦1,000 with bank details). Safe to run twice."""
    from apps.payments.services import run_weekly_payouts
    created = run_weekly_payouts(request.user)
    log_action(request, "payouts.run", request.user, {"count": len(created), "total_amount": sum(p.amount for p in created)})
    return created


def mark_payout_paid(request, payout, reference: str = ""):
    from apps.payments.models import PayoutStatus
    from apps.payments.services import mark_payout
    if reference:
        payout.reference = reference
    mark_payout(payout, PayoutStatus.PAID, request.user)
    log_action(request, "payout.paid", payout, {"reference": payout.reference, "amount": payout.amount})
    notify(payout.provider.user, "Payout sent", f"₦{payout.amount // 100:,} is on its way to your bank.",
           data={"type": "payout", "payout_id": str(payout.id)})
    return payout


def mark_payout_failed(request, payout, reason: str):
    """The amount (and any instant fee) goes back to the driver/rider's balance."""
    from apps.payments.models import PayoutStatus
    from apps.payments.services import mark_payout
    mark_payout(payout, PayoutStatus.FAILED, request.user, reason)
    log_action(request, "payout.failed", payout, {"reason": reason})
    return payout


# =========================================================================== broadcasts
BROADCAST_MAX = 20_000   # above this, send from a background job instead of a web request


def broadcast_audience(audience: str):
    """Users for a broadcast audience key (see dashboard BroadcastForm.AUDIENCES)."""
    from datetime import timedelta
    recent = timezone.now() - timedelta(days=90)
    active = User.objects.filter(status="active", is_active=True)
    if audience == "customers":
        return active.filter(customer_profile__isnull=False)
    if audience in ("customers_car", "customers_bike"):
        service = audience.split("_")[1]
        ids = Ride.objects.filter(service=service, requested_at__gte=recent).values("customer_id")
        return active.filter(pk__in=ids)
    if audience == "drivers":
        return active.filter(provider_profile__service="car", provider_profile__status=ProviderStatus.APPROVED)
    if audience == "riders":
        return active.filter(provider_profile__service="bike", provider_profile__status=ProviderStatus.APPROVED)
    if audience == "providers_online":
        return active.filter(provider_profile__is_online=True)
    raise ApiError("validation_error", "Unknown audience.", details={"audience": ["Choose an audience."]})


def send_broadcast(request, audience: str, title: str, body: str) -> dict:
    """
    Inbox notification + push to everyone in the audience. Returns delivery counts, which are
    also stored in the audit log (that's the broadcast history). Pushes that fail are counted,
    never raised, so one bad token can't stop the rest.
    """
    from apps.accounts.models import Device
    from apps.support.models import Notification
    from apps.support.services import send_push

    users = list(broadcast_audience(audience).only("id", "phone"))
    if not users:
        raise Conflict("empty_audience", "Nobody is in that audience right now, so nothing was sent.")
    if len(users) > BROADCAST_MAX:
        raise Conflict("audience_too_large", f"That audience has {len(users):,} people. Send it from a background job instead.")
    data = {"type": "broadcast"}
    Notification.objects.bulk_create([Notification(user=u, title=title, body=body, data=data) for u in users], batch_size=1000)
    with_device = set(Device.objects.filter(user__in=users).values_list("user_id", flat=True))
    pushed = failed = 0
    for u in users:
        if u.pk not in with_device:
            continue
        try:
            send_push(u, title, body, data)
            pushed += 1
        except Exception:   # noqa: BLE001 — count and carry on
            failed += 1
    result = {"audience": audience, "recipients": len(users), "push_sent": pushed, "push_failed": failed,
              "no_device": len(users) - len(with_device & {u.pk for u in users}), "title": title, "body": body}
    log_action(request, "broadcast.send", request.user, result)
    return result
