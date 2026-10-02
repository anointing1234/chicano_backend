"""
Rides (Customers & Rides panel) and Live map & dispatch (Platform).

Rides list: search by ride ID / phone / plate / address; filters for status, "needs attention",
business line, ride type, city, payment method and status, date range; server-side sorting;
CSV export of the filtered list. Ride page: route, people, money, ratings, dispatch attempts,
timeline; refund and cancel.
Dispatch: live map + queue of waiting rides (manual ones first) + nearest candidates + assign.
"""
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.db.models import CharField, Q
from django.db.models.functions import Cast
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from apps.core.roles import has_area
from apps.pricing.models import RideType
from apps.providers.models import ProviderProfile
from apps.rides import dispatch as dispatch_engine
from apps.rides.models import PaymentStatus, Ride, RideStatus
from apps.staff import services

from .. import metrics
from ..access import apply_sort, csv_response, current_service, is_fragment, paginate, run_action, staff_area
from ..forms import RefundForm

LIVE = "searching,accepted,arrived,in_progress"
SORTS = {"requested": "requested_at", "fare": "total_amount", "status": "status", "completed": "completed_at"}
STATUS_OPTIONS = [("", "Any status"), (LIVE, "Live now"), ("searching", "Searching"), ("completed", "Completed"),
                  ("cancelled", "Cancelled"), ("no_provider", "No driver found"), ("scheduled", "Scheduled")]
ATTENTION_OPTIONS = [("", "All rides"), ("manual", "Needs manual dispatch"), ("slow", "Searching over 5 minutes"),
                     ("payment", "Payment failed"), ("refunded", "Refunded"), ("cash", "Cash not yet collected")]


def _ride(pk) -> Ride:
    return get_object_or_404(Ride.objects.select_related("customer", "provider__user", "vehicle", "ride_type", "quote__promo"), pk=pk)


def _filtered(request):
    service = current_service(request)
    g = request.GET
    qs = Ride.objects.select_related("ride_type", "customer", "provider__user", "vehicle")
    if service:
        qs = qs.filter(service=service)
    if g.get("status"):
        qs = qs.filter(status__in=g["status"].split(","))
    attention = g.get("attention")
    if attention == "manual":
        qs = qs.filter(status=RideStatus.SEARCHING, needs_manual_dispatch=True)
    elif attention == "slow":
        qs = qs.filter(status=RideStatus.SEARCHING, requested_at__lt=timezone.now() - timedelta(minutes=5))
    elif attention == "payment":
        qs = qs.filter(payment_status=PaymentStatus.FAILED)
    elif attention == "refunded":
        qs = qs.filter(refunded_amount__gt=0)
    elif attention == "cash":
        qs = qs.filter(status=RideStatus.COMPLETED, payment_method="cash", cash_collected_at__isnull=True)
    for key, field in (("ride_type", "ride_type__code"), ("city", "city"), ("payment", "payment_method"), ("pay_status", "payment_status")):
        if g.get(key):
            qs = qs.filter(**{field: g[key]})
    if parse_date(g.get("from", "") or ""):
        qs = qs.filter(requested_at__date__gte=parse_date(g["from"]))
    if parse_date(g.get("to", "") or ""):
        qs = qs.filter(requested_at__date__lte=parse_date(g["to"]))
    if g.get("q"):
        s = g["q"].strip()
        if s.upper().startswith("TR-"):          # the trip reference shown on the boards
            s = s[3:]
        cond = (Q(customer__phone__icontains=s.lstrip("0").replace(" ", "")) | Q(provider__user__phone__icontains=s.lstrip("0").replace(" ", ""))
                | Q(customer__first_name__icontains=s) | Q(customer__last_name__icontains=s)
                | Q(provider__user__first_name__icontains=s) | Q(provider__user__last_name__icontains=s)
                | Q(pickup_address__icontains=s) | Q(dropoff_address__icontains=s) | Q(vehicle__plate_number__icontains=s.replace(" ", "")))
        try:
            cond |= Q(pk=uuid.UUID(s))
        except ValueError:
            if len(s) >= 6 and all(c in "0123456789abcdef-" for c in s.lower()):
                # The short ID shown in lists (first characters of the UUID), compared as text.
                qs = qs.annotate(id_text=Cast("id", CharField()))
                cond |= Q(id_text__startswith=s.lower())
        qs = qs.filter(cond)
    return apply_sort(request, qs, SORTS, "-requested")


@staff_area("rides.view")
def trip_list(request):
    qs, sort = _filtered(request)
    if request.GET.get("export") == "csv":
        rows = ([str(r.pk), r.requested_at.isoformat(), r.service, r.ride_type.name, r.status, r.city, r.customer.full_name,
                 r.customer.phone, r.provider.user.full_name if r.provider else "", r.pickup_address, r.dropoff_address,
                 f"{r.total_amount / 100:.2f}", f"{r.tip_amount / 100:.2f}", f"{r.commission_amount / 100:.2f}",
                 f"{r.refunded_amount / 100:.2f}", r.payment_method, r.payment_status] for r in qs[:20000])
        return csv_response("chicano-rides.csv", ["ride_id", "requested_at", "service", "ride_type", "status", "city", "customer",
                                                  "customer_phone", "driver_or_rider", "pickup", "dropoff", "total_naira", "tip_naira",
                                                  "commission_naira", "refunded_naira", "payment_method", "payment_status"], rows)
    return render(request, "dashboard/trips/list.html", {
        "page": paginate(request, qs), "sort": sort, "f": request.GET, "status_options": STATUS_OPTIONS,
        "attention_options": ATTENTION_OPTIONS, "ride_types": RideType.objects.order_by("service", "sort_order"),
        "cities": metrics.cities(), "pay_statuses": PaymentStatus.choices, "panel": "customer"})


EVENT_STYLE = {   # RideEvent.event -> (title, timeline colour)
    "requested": ("Requested", "c-grey"), "offer_sent": ("Offer sent", "c-grey"), "offer_declined": ("Offer declined", "c-grey"),
    "offer_expired": ("Offer expired", "c-grey"), "manual_assign": ("Assigned by staff", "c-blue"),
    "accepted": ("{who} assigned", "c-blue"), "arrived": ("{who} at pickup", "c-blue"),
    "package_collected": ("Package collected", "c-orange"), "helmet_handed_over": ("Package collected", "c-orange"),
    "in_progress": ("{started}", "c-blue"), "completed": ("Completed", "c-green"), "no_provider": ("No {lower} found", "c-red"),
    "cancelled_by_customer": ("Cancelled by the customer", "c-red"), "cancelled_by_provider": ("{who} cancelled", "c-red"),
    "cancelled_by_staff": ("Cancelled by staff", "c-red"), "no_show": ("{noshow}", "c-red"), "sos_raised": ("SOS raised", "c-red"),
    "searching": ("Searching again", "c-orange"),
}


def _timeline(ride) -> list[dict]:
    """The ride's events, oldest first, in plain words with a detail line (A03 'Event timeline')."""
    who = "Rider" if ride.service == "bike" else "Driver"
    words = {"who": who, "lower": who.lower(), "started": "Delivery started" if ride.service == "bike" else "Trip started",
             "noshow": "Sender not available" if ride.service == "bike" else "Customer no-show"}
    out = []
    for e in ride.events.select_related("actor").order_by("created_at"):
        title, cls = EVENT_STYLE.get(e.event, (e.event.replace("_", " ").capitalize(), "c-grey"))
        title = title.format(**words)
        d, sub = e.data or {}, ""
        if e.event == "requested":
            promo = f" · promo {ride.quote.promo.code}" if ride.quote_id and ride.quote and ride.quote.promo else ""
            upfront = d.get("total_amount") or (ride.quote.total_amount if ride.quote_id and ride.quote else ride.total_amount)
            sub = f"{ride.ride_type.name} · upfront fare ₦{int(upfront) / 100:,.0f}{promo}"
        elif e.event in ("accepted", "manual_assign", "offer_sent", "offer_declined") and d.get("provider_id"):
            prov = ProviderProfile.objects.select_related("user").filter(pk=d["provider_id"]).first()
            if prov:
                plate = prov.vehicles.filter(is_active=True).values_list("plate_number", flat=True).first() or ""
                km = f" · {float(d['km']):.1f} km away" if d.get("km") else ""
                sub = f"{prov.user.first_name} {prov.user.last_name[:1]}.{f' · {plate}' if plate else ''}{km}"
        elif e.event == "arrived":
            sub = f"Pickup: {ride.pickup_address}"
        elif e.event in ("package_collected", "helmet_handed_over") and ride.package_kind:
            sub = f"{ride.package_kind}, {ride.package_size} · for {ride.recipient_name}"
        elif e.event == "in_progress":
            sub = f"Heading to {ride.recipient_name or ride.dropoff_address}"
        elif e.event == "completed":
            sub = f"Final fare ₦{ride.total_amount / 100:,.0f} · {ride.get_payment_method_display().lower()}"
        elif d.get("reason"):
            sub = f"“{d['reason']}”"
        if e.actor and e.event.startswith("cancelled_by_staff"):
            sub = (sub + " · " if sub else "") + f"by {e.actor.full_name}"
        out.append({"title": title, "cls": cls, "sub": sub, "at": e.created_at})
    return out


def _age(dt) -> str:
    mins = int((timezone.now() - dt).total_seconds() // 60)
    return f"{mins} min" if mins < 60 else (f"{mins // 60} h" if mins < 2880 else f"{mins // 1440} days")


@staff_area("rides.view")
def trip_detail(request, pk):
    ride = _ride(pk)
    refundable = ride.total_amount + ride.tip_amount - ride.refunded_amount
    dispute = (ride.tickets.filter(category__in=["fare", "driver", "safety", "other"]).exclude(status__in=["resolved", "closed"])
               .select_related("user").order_by("created_at").first())
    quoted = ride.quote.total_amount if ride.quote_id and ride.quote else None
    charged = ride.total_amount + ride.tip_amount
    difference = max(0, min(refundable, charged - quoted)) if quoted is not None else 0
    first_msg = dispute.messages.filter(is_internal_note=False).order_by("created_at").first() if dispute else None
    return render(request, "dashboard/trips/detail.html", {
        "ride": ride, "timeline": _timeline(ride), "dispute": dispute, "dispute_msg": first_msg,
        "quoted": quoted, "charged": charged, "difference": difference, "now": timezone.now(),
        "dispute_age": _age(dispute.created_at) if dispute else "", "default_refund": f"{(difference or refundable) / 100:.2f}",
        "events": ride.events.select_related("actor").order_by("-created_at"),
        "offers": ride.offers.select_related("provider__user").order_by("created_at"),
        "ratings": ride.ratings.all(), "stops": ride.stops.all(),
        "payments": ride.payments.order_by("-created_at"),
        "tickets": ride.tickets.order_by("-created_at"),
        "refundable": refundable,
        "can_refund_now": ride.status in (RideStatus.COMPLETED, RideStatus.CANCELLED) and refundable > 0,
        "can_cancel_now": ride.status in (RideStatus.SCHEDULED, RideStatus.SEARCHING, RideStatus.ACCEPTED, RideStatus.ARRIVED),
        "refund_form": RefundForm(initial={"amount": f"{refundable / 100:.2f}"})})


@require_POST
@staff_area("rides.refund")
def trip_refund(request, pk):
    ride = _ride(pk)
    form = RefundForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please enter an amount and a reason for the refund.")
    else:
        amount = form.kobo()
        full = amount == ride.total_amount + ride.tip_amount - ride.refunded_amount
        ok = run_action(request, lambda: services.refund(request, ride, amount, form.cleaned_data["reason"], form.cleaned_data["claw_back"]),
                        f"{'Full' if full else 'Partial'} refund of ₦{form.cleaned_data['amount']:,.2f} processed.")
        _close_dispute(request, ride, f"We've refunded ₦{form.cleaned_data['amount']:,.2f}. {form.cleaned_data['reason']}" if ok else None)
    return redirect("dashboard:trip_detail", pk=pk)


def _close_dispute(request, ride, body):
    """Resolve the open dispute ticket (when the form sent `resolve_ticket`) with a reply the customer sees."""
    ticket_id = request.POST.get("resolve_ticket")
    if not body or not ticket_id or not has_area(request.user, "support"):
        return
    try:
        ticket = ride.tickets.filter(pk=uuid.UUID(ticket_id)).first()
    except ValueError:
        return
    if ticket and ticket.status not in ("resolved", "closed"):
        services.reply_ticket(request, ticket, body, False, "resolved")


@require_POST
@staff_area("support")
def trip_no_refund(request, pk):
    """Resolution 'No refund': answer the dispute and resolve it (A03)."""
    ride = _ride(pk)
    reason = request.POST.get("reason", "").strip()
    if not reason:
        messages.error(request, "Tell the customer why there's no refund.")
    else:
        _close_dispute(request, ride, reason)
        messages.success(request, "Dispute resolved without a refund. The customer got your reply.")
    return redirect("dashboard:trip_detail", pk=pk)


@require_POST
@staff_area("rides.cancel")
def trip_cancel(request, pk):
    reason = request.POST.get("reason", "").strip()
    if not reason:
        messages.error(request, "Please give a reason for cancelling.")
    else:
        run_action(request, lambda: services.cancel_ride(request, _ride(pk), reason), "Ride cancelled. The customer isn't charged.")
    return redirect("dashboard:trip_detail", pk=pk)


# =========================================================================== dispatch
@staff_area("dispatch")
def dispatch(request):
    """Live map + queue (refreshes every 5 s) + candidates for the selected ride (?ride=<id>)."""
    dispatch_engine.tick()   # keep offers/timeouts moving even without the background worker
    service = current_service(request)
    from django.db.models import Count
    queue = (Ride.objects.filter(status=RideStatus.SEARCHING).select_related("ride_type", "customer")
             .annotate(declines=Count("offers", filter=Q(offers__status__in=["declined", "expired"])))
             .order_by("-needs_manual_dispatch", "requested_at"))
    if service:
        queue = queue.filter(service=service)
    if request.GET.get("city"):
        queue = queue.filter(city=request.GET["city"])
    if request.GET.get("ride_type"):
        queue = queue.filter(ride_type__code=request.GET["ride_type"])
    selected = None
    try:
        selected = Ride.objects.select_related("ride_type", "customer").filter(pk=uuid.UUID(request.GET.get("ride", ""))).first()
    except ValueError:
        pass
    if selected is None and not is_fragment(request):
        selected = queue.first()          # the board always suggests someone for the top request (A07)
    now = timezone.now()
    rides = list(queue[:100])
    for r in rides:
        r.wait_s = int((now - r.requested_at).total_seconds())
        r.wait_label = f"{r.wait_s // 60}:{r.wait_s % 60:02d}"
    context = {"queue": rides, "queue_total": queue.count(), "manual_total": queue.filter(needs_manual_dispatch=True).count(),
               "selected": selected, "f": request.GET, "cities": metrics.cities(), "panel": "platform",
               "ride_types": RideType.objects.filter(is_active=True).order_by("service", "sort_order")}
    if is_fragment(request):
        return render(request, "dashboard/partials/dispatch_queue.html", context)
    if selected and selected.status == RideStatus.SEARCHING:
        candidates = []
        wide = request.GET.get("wide") == "1"
        radius = WIDE_RADIUS_KM if wide else settings.DISPATCH_RADIUS_KM
        context.update(wide=wide, radius_km=int(radius), wide_url=f"?ride={selected.pk}&wide=1")
        from apps.core.geo import eta_seconds
        for provider, dist in dispatch_engine.find_candidates(selected, limit=20, radius_km=radius):
            provider.distance_km = round(dist, 1)
            provider.eta_min = max(1, round(eta_seconds(dist, selected.service) / 60))
            provider.accept_pct = round(100 * provider.offers_accepted / provider.offers_received) if provider.offers_received else None
            provider.active_vehicle = next((v for v in provider.vehicles.all() if v.is_active), None)
            candidates.append(provider)
        context["candidates"] = candidates
        context["pending_offer"] = selected.offers.filter(status="sent").select_related("provider__user").first()
    return render(request, "dashboard/dispatch.html", context)


WIDE_RADIUS_KM = 25     # "Search wider" on Dispatch when nobody is within DISPATCH_RADIUS_KM


@require_POST
@staff_area("dispatch")
def dispatch_assign(request, pk):
    ride = _ride(pk)
    try:
        provider = ProviderProfile.objects.select_related("user").get(pk=request.POST.get("provider_id"))
    except (ProviderProfile.DoesNotExist, ValueError, TypeError):
        raise Http404("That driver/rider no longer exists.")
    run_action(request, lambda: services.assign_ride(request, ride, provider),
               f"Offer sent to {provider.user.full_name}. They have 60 seconds to accept.")
    return redirect(f"{reverse('dashboard:dispatch')}?ride={ride.pk}")
