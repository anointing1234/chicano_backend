"""
Administration: broadcasts, fares / ride types / service zones, staff & roles, audit log,
and the global search in the header.
"""
import secrets

import uuid

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import CharField, Q
from django.db.models.functions import Cast
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_date

from apps.core.exceptions import ApiError
from apps.core.models import AuditLog
from apps.core.roles import ROLE_DESCRIPTIONS, has_area, role_matrix
from apps.pricing.models import FareRule, RideType, ServiceZone
from apps.providers.models import ProviderProfile
from apps.rides.models import Ride
from apps.staff import services
from apps.support.models import SupportTicket

from ..access import current_service, paginate, staff_area
from ..forms import AUDIENCES, BroadcastForm, FareRuleForm, RideTypeForm, ServiceZoneForm, StaffMemberForm
from ._crud import edit_record

User = get_user_model()
SESSION_BROADCAST_TOKEN = "dashboard_broadcast_token"


# =========================================================================== broadcasts
@staff_area("broadcasts")
def broadcasts(request):
    """
    Compose → preview → confirm → send. A one-time token in the session makes a second submit
    (double click, browser resend) do nothing, so nobody gets the same message twice.
    """
    counts = {key: services.broadcast_audience(key).count() for key, _ in AUDIENCES}
    if request.method == "POST":
        form = BroadcastForm(request.POST)
        form.fields["audience"].choices = [(k, f"{label} ({counts[k]:,})") for k, label in AUDIENCES]
        expected = request.session.pop(SESSION_BROADCAST_TOKEN, None)
        if form.is_valid():
            if not expected or form.cleaned_data["token"] != expected:
                messages.error(request, "This message was already sent (or the page expired). Nothing was sent twice.")
                return redirect("dashboard:broadcasts")
            try:
                result = services.send_broadcast(request, form.cleaned_data["audience"], form.cleaned_data["title"], form.cleaned_data["body"])
            except ApiError as exc:
                messages.error(request, str(exc.detail))
            else:
                messages.success(request, f"Broadcast sent to {result['recipients']:,} people · {result['push_sent']:,} push notifications delivered"
                                          + (f", {result['push_failed']:,} failed" if result["push_failed"] else "") + ".")
                return redirect("dashboard:broadcasts")
        else:
            messages.error(request, "Please correct the highlighted fields.")
    else:
        form = BroadcastForm()
        form.fields["audience"].choices = [(k, f"{label} ({counts[k]:,})") for k, label in AUDIENCES]
    token = secrets.token_urlsafe(16)
    request.session[SESSION_BROADCAST_TOKEN] = token
    form.initial["token"] = token
    form.data = form.data.copy() if form.is_bound else form.data
    if form.is_bound:
        form.data["token"] = token
    history = AuditLog.objects.filter(action="broadcast.send").select_related("actor").order_by("-created_at")[:20]
    return render(request, "dashboard/admin/broadcasts.html", {"form": form, "history": history, "panel": "admin"})


# =========================================================================== fares, ride types, zones
@staff_area("settings.view")
def settings_home(request):
    service = current_service(request)
    tab = request.GET.get("tab", "fares")
    ctx = {"tab": tab, "panel": "admin"}
    if tab == "ride-types":
        qs = RideType.objects.order_by("service", "sort_order")
    elif tab == "zones":
        qs = ServiceZone.objects.order_by("city", "service", "name")
    else:
        ctx["tab"] = "fares"
        qs = FareRule.objects.select_related("ride_type").order_by("city", "ride_type__service", "ride_type__sort_order", "-is_active")
        if service:
            qs = qs.filter(ride_type__service=service)
    if service and ctx["tab"] != "fares":
        qs = qs.filter(service=service)
    ctx["rows"] = qs
    return render(request, "dashboard/admin/settings.html", ctx)


def _settings(tab):
    return reverse("dashboard:settings") + f"?tab={tab}"


@staff_area("settings.edit")
def fare_edit(request, pk=None):
    rule = get_object_or_404(FareRule, pk=pk) if pk else None

    def switch_off_previous(obj):
        # Keep "one active rule per ride type + city": the new active rule replaces the old one.
        if obj.is_active:
            FareRule.objects.filter(ride_type=obj.ride_type, city=obj.city, is_active=True).exclude(pk=obj.pk).update(is_active=False)

    return edit_record(request, FareRuleForm, rule, title="Edit fare rule" if rule else "New fare rule", back=_settings("fares"),
                       audit_name="fare_rule", before_save=switch_off_previous, noun="fare rule", crumb=("Fares", _settings("fares")),
                       intro="Prices for one ride type in one city. New prices apply to new fare estimates immediately.")


@staff_area("settings.edit")
def ride_type_edit(request, pk=None):
    obj = get_object_or_404(RideType, pk=pk) if pk else None
    return edit_record(request, RideTypeForm, obj, title=obj.name if obj else "New ride type", back=_settings("ride-types"),
                       audit_name="ride_type", noun="ride type", crumb=("Ride types", _settings("ride-types")),
                       intro="An option in the “Choose a ride” sheet. Add a fare rule for it before switching it on.")


@staff_area("settings.edit")
def zone_edit(request, pk=None):
    obj = get_object_or_404(ServiceZone, pk=pk) if pk else None
    return edit_record(request, ServiceZoneForm, obj, title=obj.name if obj else "New service zone", back=_settings("zones"),
                       audit_name="service_zone", noun="service zone", crumb=("Service zones", _settings("zones")),
                       intro="Where a service may pick up and drop off. Bookings outside every active zone are refused.")


# =========================================================================== staff & roles
@staff_area("team")
def staff(request):
    qs = User.objects.filter(is_staff=True).order_by("status", "first_name")
    if request.GET.get("role"):
        qs = qs.filter(staff_role=request.GET["role"])
    return render(request, "dashboard/admin/staff.html", {
        "rows": qs, "matrix": role_matrix(), "roles": list(ROLE_DESCRIPTIONS.items()), "f": request.GET, "panel": "admin"})


@staff_area("team")
def staff_edit(request, pk=None):
    obj = get_object_or_404(User, pk=pk, is_staff=True) if pk else None
    if obj and obj.pk == request.user.pk and request.method == "POST" and request.POST.get("staff_role") != "super_admin":
        messages.error(request, "You can't remove your own super admin role. Ask another super admin.")
        return redirect("dashboard:staff_edit", pk=pk)
    return edit_record(request, StaffMemberForm, obj, title=obj.full_name if obj else "Add staff member", back=reverse("dashboard:staff"),
                       audit_name="staff", noun="staff account", crumb=("Staff & roles", reverse("dashboard:staff")),
                       intro="Staff sign in to this dashboard with their email and password. To remove access, suspend the account under All accounts.")


# =========================================================================== audit log
@staff_area("audit")
def audit(request):
    g = request.GET
    qs = AuditLog.objects.select_related("actor").order_by("-created_at")
    if g.get("action"):
        qs = qs.filter(action__startswith=g["action"].strip())
    if g.get("actor"):
        try:
            qs = qs.filter(actor_id=uuid.UUID(g["actor"]))
        except ValueError:                      # a mangled link shouldn't crash the page
            qs = qs.none()
    if g.get("target"):
        qs = qs.filter(Q(target_id__startswith=g["target"].strip()) | Q(target_type__iexact=g["target"].strip()))
    if parse_date(g.get("from", "") or ""):
        qs = qs.filter(created_at__date__gte=parse_date(g["from"]))
    if parse_date(g.get("to", "") or ""):
        qs = qs.filter(created_at__date__lte=parse_date(g["to"]))
    actions = sorted({a.split(".")[0] for a in AuditLog.objects.values_list("action", flat=True).distinct()[:500]})
    return render(request, "dashboard/admin/audit.html", {
        "page": paginate(request, qs, per_page=50), "f": g, "actions": actions,
        "staff": User.objects.filter(is_staff=True).order_by("first_name"), "panel": "admin"})


# =========================================================================== global search
@staff_area("overview")
def search(request):
    """Header search: people (phone/name/email), drivers by plate, rides by ID, tickets by subject."""
    q = request.GET.get("q", "").strip()
    results = {"people": [], "providers": [], "rides": [], "tickets": []}
    if len(q) >= 2:
        phone = q.lstrip("0").replace(" ", "")
        if has_area(request.user, "users.view"):
            results["people"] = User.objects.filter(Q(phone__icontains=phone) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
                                                    | Q(email__icontains=q)).select_related("provider_profile")[:10]
        if has_area(request.user, "providers.view"):
            results["providers"] = (ProviderProfile.objects.filter(Q(vehicles__plate_number__icontains=q.replace(" ", "")) | Q(user__phone__icontains=phone)
                                                                   | Q(user__first_name__icontains=q) | Q(user__last_name__icontains=q))
                                    .select_related("user").distinct()[:10])
        if has_area(request.user, "rides.view") and len(q) >= 6 and all(c in "0123456789abcdef-" for c in q.lower()):
            results["rides"] = (Ride.objects.annotate(id_text=Cast("id", CharField())).filter(id_text__startswith=q.lower())
                                .select_related("customer", "ride_type")[:10])
        if has_area(request.user, "support"):
            results["tickets"] = SupportTicket.objects.filter(subject__icontains=q).select_related("user")[:10]
    total = sum(len(v) for v in results.values())
    return render(request, "dashboard/admin/search.html", {"q": q, "results": results, "total": total, "panel": "platform"})
