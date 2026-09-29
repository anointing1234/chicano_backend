"""
Values every dashboard template can use:

    nav_groups     sidebar sections for this staff role (Platform · Customers & Rides · Drivers & Fleet · Administration)
    panel          which panel the current page belongs to ("platform" | "customer" | "driver" | "admin")
    page_label     the active menu item's label (used as the header title when a page doesn't set one)
    service        the All / Cars / Bikes switch
    alerts         header notifications: things waiting for this person, with counts and links
    panels         the panel switcher (dashboards this person can open)
    can            role flags for templates, e.g. {% if can.rides_refund %}
"""
from django.urls import reverse

from apps.core.roles import STAFF_AREAS, has_area, is_staff_member

# (group key, title, [(url name, label, icon, role area)])
NAV = [
    ("platform", "Platform", [
        ("dashboard:overview", "Platform overview", "grid", "overview"),
        ("dashboard:dispatch", "Live map & dispatch", "radar", "dispatch"),
        ("dashboard:reports", "Reports & analytics", "chart", "reports"),
    ]),
    ("customer", "Customers & Rides", [
        ("dashboard:customer_dashboard", "Customer dashboard", "gauge", "users.view"),
        ("dashboard:customers", "Manage customers", "people", "users.view"),
        ("dashboard:trips", "Rides", "route", "rides.view"),
        ("dashboard:payments", "Payments & refunds", "card", "payments.view"),
        ("dashboard:promotions", "Promo codes", "tag", "growth"),
        ("dashboard:support", "Support tickets & SOS", "lifebuoy", "support"),
        ("dashboard:lost_items", "Lost items", "bag", "support"),
    ]),
    ("driver", "Drivers & Fleet", [
        ("dashboard:driver_dashboard", "Driver dashboard", "gauge", "providers.view"),
        ("dashboard:providers", "Manage drivers & riders", "wheel", "providers.view"),
        ("dashboard:documents", "Applications & documents", "doc", "providers.approve"),
        ("dashboard:payouts", "Payouts & commission", "bank", "payouts"),
        ("dashboard:incentives", "Driver incentives", "trophy", "growth"),
    ]),
    ("admin", "Administration", [
        ("dashboard:broadcasts", "Broadcasts", "megaphone", "broadcasts"),
        ("dashboard:users", "All accounts", "id", "users.view"),
        ("dashboard:settings", "Fares, ride types & zones", "gear", "settings.view"),
        ("dashboard:staff", "Staff & roles", "shield", "team"),
        ("dashboard:audit", "Audit log", "list", "audit"),
    ]),
]

PANEL_LABELS = {"platform": "Platform", "customer": "Customers & Rides", "driver": "Drivers & Fleet", "admin": "Administration"}


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
                      reverse("dashboard:support") + "?assigned=none", "lifebuoy", False))
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
    from .access import SESSION_SERVICE_KEY

    user = request.user
    path = request.path
    groups, best = [], (0, None, None)
    for key, title, items in NAV:
        visible = []
        for name, label, icon, area in items:
            if not has_area(user, area):
                continue
            url = reverse(name)
            # The longest matching URL wins, so /dashboard/ (overview) doesn't light up on every page.
            if (path == url or (url != "/dashboard/" and path.startswith(url))) and len(url) > best[0]:
                best = (len(url), name, (key, label))
            visible.append({"name": name, "url": url, "label": label, "icon": icon})
        if visible:
            groups.append({"key": key, "title": title, "items": visible})
    for g in groups:
        for item in g["items"]:
            item["active"] = item["name"] == best[1]
        g["open"] = True

    panel, page_label = (best[2] if best[2] else ("platform", "Dashboard"))
    alerts = _alerts(user)
    panels = [p for p in [
        ("platform", "Platform overview", reverse("dashboard:overview"), "overview"),
        ("customer", "Customers & Rides", reverse("dashboard:customer_dashboard"), "users.view"),
        ("driver", "Drivers & Fleet", reverse("dashboard:driver_dashboard"), "providers.view"),
    ] if has_area(user, p[3])]

    return {
        "nav_groups": groups,
        "panel": panel,
        "panel_label": PANEL_LABELS[panel],
        "page_label": page_label,
        "service": request.session.get(SESSION_SERVICE_KEY, "all"),
        "alerts": alerts,
        "alerts_total": sum(a["count"] for a in alerts),
        "alerts_urgent": any(a["urgent"] for a in alerts),
        "open_sos_count": next((a["count"] for a in alerts if a["label"] == "Open SOS alerts"), 0),
        "panels": [{"key": k, "label": lbl, "url": u} for k, lbl, u, _ in panels],
        # Template-friendly role flags: {% if can.rides_refund %} (dots become underscores).
        "can": {area.replace(".", "_"): has_area(user, area) for area in STAFF_AREAS},
    }
