"""
Values every dashboard template can use:

    nav_groups     sidebar sections for this staff role, as in the design: People · Operations · Money & growth · Admin
    page_label     the active menu item's label
    service        the All / Cars / Bikes switch
    service_scope  "Cars + Bikes", "Cars" or "Bikes" (shown under "Super Admin" in the sidebar)
    alerts         header notifications: things waiting for this person, with counts and links
    can            role flags for templates, e.g. {% if can.rides_refund %}
    bottom_nav     phone bottom bar (Today · Trips · Dispatch · Support · More)
    cities         service cities (top-bar city menu)
"""
from django.urls import reverse

from apps.core.roles import STAFF_AREAS, has_area, is_staff_member

# (section title, [(key, url name, query, label, icon, role area, count key)])
NAV = [
    ("People", [
        ("overview", "dashboard:overview", "", "Overview", "grid", "overview", None),
        ("users", "dashboard:customers", "", "Users", "people", "users.view", None),
        ("riders", "dashboard:providers", "kind=bike", "Riders · bikes", "bike", "providers.view", None),
        ("drivers", "dashboard:providers", "kind=car", "Drivers · cars", "car", "providers.view", None),
        ("fleet", "dashboard:driver_dashboard", "", "Fleet", "wrench", "providers.view", None),
        ("documents", "dashboard:documents", "", "Documents", "doc", "providers.approve", "documents"),
    ]),
    ("Operations", [
        ("trips", "dashboard:trips", "", "Trips", "route", "rides.view", None),
        ("dispatch", "dashboard:dispatch", "", "Dispatch", "radar", "dispatch", "dispatch"),
        ("support", "dashboard:support", "", "Support", "headset", "support", "support"),
    ]),
    ("Money & growth", [
        ("payments", "dashboard:payments", "", "Payments", "card", "payments.view", None),
        ("payouts", "dashboard:payouts", "", "Payouts", "bank", "payouts", None),
        ("promotions", "dashboard:promotions", "", "Promotions", "tag", "growth", None),
        ("incentives", "dashboard:incentives", "", "Incentives", "gift", "growth", None),
    ]),
    ("Admin", [
        ("reports", "dashboard:reports", "", "Reports", "chart", "reports", None),
        ("settings", "dashboard:settings", "", "Settings", "sliders", "settings.view", None),
    ]),
]

# Pages that aren't in the sidebar light up the item they belong to (by URL name).
ACTIVE_FOR = {
    "overview": "overview", "live_data": "overview",
    "customers": "users", "customer_dashboard": "users", "users": "users", "user_detail": "users", "user_status": "users",
    "driver_dashboard": "fleet",
    "documents": "documents", "document_review": "documents",
    "trips": "trips", "trip_detail": "trips",
    "dispatch": "dispatch", "dispatch_assign": "dispatch",
    "support": "support", "ticket_detail": "support", "lost_items": "support", "broadcasts": "support",
    "payments": "payments", "payouts": "payouts",
    "promotions": "promotions", "promo_new": "promotions", "promo_edit": "promotions",
    "incentives": "incentives", "incentive_new": "incentives", "incentive_edit": "incentives",
    "reports": "reports",
    "settings": "settings", "fare_new": "settings", "fare_edit": "settings", "ride_type_new": "settings", "ride_type_edit": "settings",
    "zone_new": "settings", "zone_edit": "settings", "staff": "settings", "staff_new": "settings", "staff_edit": "settings", "audit": "settings",
}


def _counts(user) -> dict:
    """Sidebar badges (cheap counts): documents to review, rides waiting for manual dispatch, open support tickets."""
    from apps.providers.models import ProviderDocument
    from apps.rides.models import Ride
    from apps.support.models import SupportTicket
    out = {}
    if has_area(user, "providers.approve"):
        out["documents"] = ProviderDocument.objects.filter(status="pending").count()
    if has_area(user, "dispatch"):
        out["dispatch"] = Ride.objects.filter(status="searching", needs_manual_dispatch=True).count()
    if has_area(user, "support"):
        out["support"] = SupportTicket.objects.exclude(status__in=["resolved", "closed"]).count()
    return out


def _alerts(user):
    """Header notifications: live counts of work waiting for this staff member (real queries, cheap counts)."""
    from apps.payments.models import Payout
    from apps.providers.models import ProviderDocument
    from apps.rides.models import Ride
    from apps.support.models import SOSAlert, SupportTicket

    items = []
    if has_area(user, "support"):
        items.append(("Open SOS alerts", SOSAlert.objects.filter(status="open").count(), reverse("dashboard:support") + "?tab=sos", "alert", True))
        items.append(("Unassigned tickets", SupportTicket.objects.filter(status="open", assigned_to__isnull=True).count(),
                      reverse("dashboard:support") + "?assigned=none", "headset", False))
    if has_area(user, "dispatch"):
        items.append(("Rides needing manual dispatch", Ride.objects.filter(status="searching", needs_manual_dispatch=True).count(),
                      reverse("dashboard:dispatch"), "radar", True))
    if has_area(user, "providers.approve"):
        items.append(("Documents waiting for review", ProviderDocument.objects.filter(status="pending").count(),
                      reverse("dashboard:documents"), "doc", False))
    if has_area(user, "payouts"):
        items.append(("Failed payouts", Payout.objects.filter(status="failed").count(), reverse("dashboard:payouts") + "?status=failed", "bank", True))
    if has_area(user, "payments.view"):
        items.append(("Rides with failed payment", Ride.objects.filter(payment_status="failed").count(),
                      reverse("dashboard:payments") + "?tab=failed", "card", True))
    return [{"label": lbl, "count": c, "url": u, "icon": i, "urgent": urgent and c > 0} for lbl, c, u, i, urgent in items]


def dashboard(request):
    if not request.path.startswith("/dashboard/") or not is_staff_member(getattr(request, "user", None)):
        return {}
    from . import metrics
    from .access import SESSION_SERVICE_KEY

    user = request.user
    match = getattr(request, "resolver_match", None)
    url_name = match.url_name if match else ""
    active = ACTIVE_FOR.get(url_name, "")
    if url_name in ("providers", "provider_detail", "provider_status", "provider_approve"):
        kind = request.GET.get("kind")
        if not kind and url_name != "providers":
            from apps.providers.models import ProviderProfile
            pk = match.kwargs.get("pk") if match else None
            kind = ProviderProfile.objects.filter(pk=pk).values_list("service", flat=True).first() if pk else None
        active = "riders" if kind == "bike" else "drivers"

    counts = _counts(user)
    groups, page_label = [], "Dashboard"
    for title, items in NAV:
        visible = []
        for key, name, query, label, icon, area, count_key in items:
            if not has_area(user, area):
                continue
            url = reverse(name) + (f"?{query}" if query else "")
            if key == active:
                page_label = label
            visible.append({"key": key, "url": url, "label": label, "icon": icon, "active": key == active,
                            "count": counts.get(count_key) if count_key else None})
        if visible:
            groups.append({"title": title, "items": visible})

    service = request.session.get(SESSION_SERVICE_KEY, "all")
    alerts = _alerts(user)
    role = user.staff_role or ""
    bottom = [("overview", "dashboard:overview", "Today", "grid", "overview"),
              ("trips", "dashboard:trips", "Trips", "route", "rides.view"),
              ("dispatch", "dashboard:dispatch", "Dispatch", "radar", "dispatch"),
              ("support", "dashboard:support", "Support", "headset", "support")]
    return {
        "nav_groups": groups,
        "nav_active": active,
        "page_label": page_label,
        "service": service,
        "service_scope": {"all": "Cars + Bikes", "car": "Cars", "bike": "Bikes"}.get(service, "Cars + Bikes"),
        "staff_scope": "full access" if role == "super_admin" else "role access",
        "alerts": alerts,
        "alerts_total": sum(a["count"] for a in alerts),
        "alerts_urgent": any(a["urgent"] for a in alerts),
        "open_sos_count": next((a["count"] for a in alerts if a["label"] == "Open SOS alerts"), 0),
        "bottom_nav": [{"key": k, "url": reverse(n), "label": lbl, "icon": i, "active": k == active}
                       for k, n, lbl, i, area in bottom if has_area(user, area)],
        "cities": metrics.cities() or ["Lagos"],
        # Template-friendly role flags: {% if can.rides_refund %} (dots become underscores).
        "can": {area.replace(".", "_"): has_area(user, area) for area in STAFF_AREAS},
    }
