"""
Dashboards: Platform overview, Customers & Rides panel, Drivers & Fleet panel, Reports (+ CSV),
and the live data used by the map and the SOS banner.

All numbers come from apps/dashboard/metrics.py (database queries). Date range: ?range=today|7d|30d|custom.
"""
from datetime import timedelta

from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from apps.core.roles import has_area
from apps.rides.models import Ride, RideStatus
from apps.staff import services
from apps.support.models import SOSAlert

from .. import metrics
from ..access import RANGES, csv_response, current_service, get_period, staff_area


def _ctx(request, **extra):
    period = get_period(request)
    return {"period": period, "ranges": RANGES, **extra}


LIVE_STATUSES = [("", "All"), ("active", "Live now"), ("searching", "Searching"), ("on_trip", "On trip"),
                 ("completed", "Completed"), ("cancelled", "Cancelled"), ("payment_failed", "Payment failed")]


@staff_area("overview")
def overview(request):
    """Overview board (A01): KPIs, completed trips by hour (cars vs bikes), needs attention, live trip requests."""
    service = current_service(request)
    period = get_period(request, default="today")
    ctx = {"period": period, "ranges": RANGES, "b": metrics.overview_board(period, service)}
    # Chart: the period by hour; ?chart=yesterday shows yesterday instead (the "Compare: yesterday" button).
    yesterday = request.GET.get("chart") == "yesterday"
    ctx["chart_yesterday"] = yesterday
    ctx["chart"] = metrics.hourly(period, service, day=timezone.localdate() - timedelta(days=1)) if yesterday else metrics.hourly(period, service)
    ctx["chart_total"] = sum(r["car"] + r["bike"] for r in ctx["chart"])
    # Live trip requests (only for staff who may see trips).
    if has_area(request.user, "rides.view"):
        live_status, live_service = request.GET.get("live_status", ""), request.GET.get("live_service", "")
        rides = Ride.objects.select_related("customer", "ride_type").order_by("-requested_at")
        if service:
            rides = rides.filter(service=service)
        if live_service in ("car", "bike"):
            rides = rides.filter(service=live_service)
        rides = {
            "active": rides.filter(status__in=[RideStatus.SEARCHING, RideStatus.ACCEPTED, RideStatus.ARRIVED, RideStatus.IN_PROGRESS]),
            "searching": rides.filter(status=RideStatus.SEARCHING), "on_trip": rides.filter(status=RideStatus.IN_PROGRESS),
            "completed": rides.filter(status=RideStatus.COMPLETED), "cancelled": rides.filter(status__in=[RideStatus.CANCELLED, RideStatus.NO_PROVIDER]),
            "payment_failed": rides.filter(payment_status="failed"),
        }.get(live_status, rides)
        ctx.update(live=rides[:5], live_count=rides.count(), live_statuses=LIVE_STATUSES, live_status=live_status,
                   live_service=live_service, now=timezone.now())
    if request.GET.get("export") == "csv":
        b = ctx["b"]
        return csv_response(f"chicano-overview-{period.date_from}-{period.date_to}.csv", ["metric", "value", "detail"], [
            ["Active users now", b["users_now"], b["users_desc"]], ["Online supply", b["online"], b["online_desc"]],
            ["Active trips", b["active"], b["active_desc"]], [b["completed_title"], b["completed"], b["completed_desc"]],
            ["Cancellation rate", b["cancel_rate"], b["cancel_desc"]], [b["revenue_title"], b["revenue"], b["revenue_desc"]],
            ["Pending approvals", b["pending"], b["pending_desc"]], ["Open support issues", b["tickets"], b["tickets_desc"]],
        ] + [[f"Trips {r['label']} cars", r["car"], ""] for r in ctx["chart"]] + [[f"Trips {r['label']} bikes", r["bike"], ""] for r in ctx["chart"]])
    return render(request, "dashboard/dashboards/overview.html", ctx)


@staff_area("users.view")
def customer_dashboard(request):
    ctx = _ctx(request, panel="customer")
    ctx["m"] = metrics.customer_panel(ctx["period"], current_service(request))
    return render(request, "dashboard/dashboards/customers.html", ctx)


@staff_area("providers.view")
def driver_dashboard(request):
    """Fleet board (A05): drivers & riders online, approvals, issues, earnings, acceptance, map, pipeline, expiring documents."""
    period = get_period(request, default="today")
    service = current_service(request)        # the top-bar All / Cars / Bikes switch also scopes the pipeline
    ctx = {"period": period, "ranges": RANGES, "f": metrics.fleet_board(period, service, service)}
    if request.GET.get("export") == "csv":
        f = ctx["f"]
        return csv_response(f"chicano-fleet-{period.date_from}.csv", ["metric", "value", "detail"], [
            ["Online drivers", f["on_car"], f["on_car_desc"]], ["Online riders", f["on_bike"], f["on_bike_desc"]],
            ["Pending approvals", f["pending"], f["pending_desc"]], ["Document / vehicle issues", f["issues"], f["issues_desc"]],
            [f["earn_title"], f["earned"], f["earned_desc"]], ["Acceptance drivers", f["acc_car"], ""], ["Acceptance riders", f["acc_bike"], ""],
            ["Pending payouts", f["payouts"], ""]] + [[f"Pipeline: {p['label']}", p["n"], ""] for p in f["pipeline"]])
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
            "lat": float(p.last_lat), "lng": float(p.last_lng), "on_trip": p.on_trip, "phase": p.phase,
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

