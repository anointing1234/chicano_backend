"""
Dashboards: Platform overview, Customers & Rides panel, Drivers & Fleet panel, Reports (+ CSV),
and the live data used by the map and the SOS banner.

All numbers come from apps/dashboard/metrics.py (database queries). Date range: ?range=today|7d|30d|custom.
"""
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from apps.core.roles import has_area
from apps.payments.models import Payment
from apps.providers.models import ProviderProfile
from apps.rides.models import Ride, RideStatus
from apps.staff import services
from apps.support.models import SOSAlert

from .. import metrics
from ..access import RANGES, csv_response, current_service, get_period, staff_area


def _ctx(request, **extra):
    period = get_period(request)
    return {"period": period, "ranges": RANGES, **extra}


@staff_area("overview")
def overview(request):
    """Platform overview: both sides of the marketplace at a glance, plus the live map."""
    service = current_service(request)
    ctx = _ctx(request)
    ctx.update(c=metrics.customer_panel(ctx["period"], service), d=metrics.driver_panel(ctx["period"], service),
               now=services.overview(service))
    # Recent activity, only for staff allowed to see it (the same checks as the full pages).
    user = request.user
    if has_area(user, "rides.view"):
        rides = Ride.objects.select_related("customer", "provider__user", "ride_type").order_by("-requested_at")
        ctx["recent_rides"] = (rides.filter(service=service) if service else rides)[:6]
    if has_area(user, "providers.view"):
        apps_ = ProviderProfile.objects.select_related("user").order_by("-created_at")
        ctx["recent_applications"] = (apps_.filter(service=service) if service else apps_)[:5]
    if has_area(user, "payments.view"):
        pays = Payment.objects.select_related("user", "ride").order_by("-created_at")
        ctx["recent_payments"] = (pays.filter(ride__service=service) if service else pays)[:6]
    return render(request, "dashboard/dashboards/overview.html", ctx)


@staff_area("users.view")
def customer_dashboard(request):
    ctx = _ctx(request, panel="customer")
    ctx["m"] = metrics.customer_panel(ctx["period"], current_service(request))
    return render(request, "dashboard/dashboards/customers.html", ctx)


@staff_area("providers.view")
def driver_dashboard(request):
    ctx = _ctx(request, panel="driver")
    ctx["m"] = metrics.driver_panel(ctx["period"], current_service(request))
    return render(request, "dashboard/dashboards/drivers.html", ctx)


@staff_area("reports")
def reports(request):
    service, city = current_service(request), request.GET.get("city") or None
    ctx = _ctx(request, city=city or "", cities=metrics.cities())
    ctx["r"] = metrics.report(ctx["period"], service, city)
    if request.GET.get("export") == "csv":
        r = ctx["r"]
        return csv_response(
            f"chicano-report-{ctx['period'].date_from}-{ctx['period'].date_to}.csv",
            ["date", "completed_trips", "gross_naira", "new_customers"],
            [[d["date"].isoformat(), d["trips"], f"{d['gross'] / 100:.2f}", d["new_customers"]] for d in r["daily"]])
    return render(request, "dashboard/dashboards/reports.html", ctx)


# =========================================================================== live data (polled)
@staff_area("overview")
def live_data(request):
    """
    JSON for static/dashboard/livemap.js: online drivers/riders with a GPS fix from the last
    10 minutes, and rides still waiting for someone. Filters: ?service=car|bike (session switch),
    ?status=available|on_trip, ?city=.
    """
    service = current_service(request)
    status, city = request.GET.get("status", ""), request.GET.get("city", "")
    pins = services.live_providers(service)
    if city:
        pins = [p for p in pins if p.city == city]
    if status == "available":
        pins = [p for p in pins if not p.on_trip]
    elif status == "on_trip":
        pins = [p for p in pins if p.on_trip]
    vehicles = {v.provider_id: v for p in pins for v in p.vehicles.all() if v.is_active} if pins else {}
    waiting = Ride.objects.filter(status=RideStatus.SEARCHING).select_related("customer")
    if service:
        waiting = waiting.filter(service=service)
    if city:
        waiting = waiting.filter(city=city)
    now = timezone.now()
    return JsonResponse({
        "drivers": [{
            "id": str(p.pk), "name": p.user.full_name, "kind": p.kind.title(), "service": p.service,
            "lat": float(p.last_lat), "lng": float(p.last_lng), "on_trip": p.on_trip,
            "updated": f"{int((now - p.last_location_at).total_seconds() // 60)} min ago",
            "vehicle": (lambda v: f"{v.color} {v.make} {v.model} · {v.plate_number}" if v else "")(vehicles.get(p.pk)),
            "url": reverse("dashboard:provider_detail", args=[p.pk]),
        } for p in pins],
        "rides": [{
            "id": str(r.pk), "lat": float(r.pickup_lat), "lng": float(r.pickup_lng), "service": r.service,
            "pickup": r.pickup_address, "dropoff": r.dropoff_address, "manual": r.needs_manual_dispatch,
            "waiting": f"waiting {int((now - r.requested_at).total_seconds() // 60)} min",
            "url": reverse("dashboard:trip_detail", args=[r.pk]),
            "assign_url": reverse("dashboard:dispatch") + f"?ride={r.pk}",
        } for r in waiting[:200]],
    })


@staff_area("support")
def sos_banner(request):
    """Fragment: the red 'open SOS' banner on every page (refreshed every 10 s)."""
    return render(request, "dashboard/partials/sos_banner.html", {"open_sos_count": SOSAlert.objects.filter(status="open").count()})

