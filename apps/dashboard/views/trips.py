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


@staff_area("rides.view")
def trip_detail(request, pk):
    ride = _ride(pk)
    refundable = ride.total_amount + ride.tip_amount - ride.refunded_amount
    return render(request, "dashboard/trips/detail.html", {
        "ride": ride, "panel": "customer",
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
        run_action(request, lambda: services.refund(request, ride, amount, form.cleaned_data["reason"], form.cleaned_data["claw_back"]),
                   f"{'Full' if full else 'Partial'} refund of ₦{form.cleaned_data['amount']:,.2f} processed.")
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
    queue = (Ride.objects.filter(status=RideStatus.SEARCHING).select_related("ride_type", "customer")
             .order_by("-needs_manual_dispatch", "requested_at"))
    if service:
        queue = queue.filter(service=service)
    if request.GET.get("city"):
        queue = queue.filter(city=request.GET["city"])
    selected = None
    try:
        selected = Ride.objects.select_related("ride_type", "customer").filter(pk=uuid.UUID(request.GET.get("ride", ""))).first()
    except ValueError:
        pass
    context = {"queue": queue[:100], "selected": selected, "f": request.GET, "cities": metrics.cities(), "panel": "platform"}
    if is_fragment(request):
        return render(request, "dashboard/partials/dispatch_queue.html", context)
    if selected and selected.status == RideStatus.SEARCHING:
        candidates = []
        wide = request.GET.get("wide") == "1"
        radius = WIDE_RADIUS_KM if wide else settings.DISPATCH_RADIUS_KM
        context.update(wide=wide, radius_km=int(radius), wide_url=f"?ride={selected.pk}&wide=1")
        for provider, dist in dispatch_engine.find_candidates(selected, limit=20, radius_km=radius):
            provider.distance_km = round(dist, 2)
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
