"""
Settings: fares, ride types, service zones (everyone reads, super admins edit), plus the staff
team and the audit log (super admins only).
"""
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from apps.core.models import AuditLog
from apps.core.roles import has_area
from apps.pricing.models import FareRule, RideType, ServiceZone

from ..access import current_service, paginate, staff_area
from ..forms import FareRuleForm, RideTypeForm, ServiceZoneForm, StaffMemberForm
from ._crud import edit_record

User = get_user_model()
TABS = ["fares", "ride-types", "zones", "team", "audit"]


@staff_area("settings.view")
def settings_home(request):
    service = current_service(request)
    tab = request.GET.get("tab", "fares")
    if tab in ("team", "audit") and not has_area(request.user, tab):
        tab = "fares"
    context = {"title": "Settings", "tab": tab}
    if tab == "fares":
        qs = FareRule.objects.select_related("ride_type").order_by("ride_type__service", "ride_type__sort_order", "-is_active")
        context["rows"] = qs.filter(ride_type__service=service) if service else qs
    elif tab == "ride-types":
        qs = RideType.objects.order_by("service", "sort_order")
        context["rows"] = qs.filter(service=service) if service else qs
    elif tab == "zones":
        qs = ServiceZone.objects.order_by("service", "name")
        context["rows"] = qs.filter(service=service) if service else qs
    elif tab == "team":
        context["rows"] = User.objects.filter(is_staff=True).order_by("first_name")
    else:
        qs = AuditLog.objects.select_related("actor").order_by("-created_at")
        if request.GET.get("action"):
            qs = qs.filter(action__startswith=request.GET["action"].strip())
        context["page"] = paginate(request, qs, per_page=50)
        context["action"] = request.GET.get("action", "")
    return render(request, "dashboard/settings/home.html", context)


def _back(tab):
    return reverse("dashboard:settings") + f"?tab={tab}"


@staff_area("settings.edit")
def fare_edit(request, pk=None):
    rule = get_object_or_404(FareRule, pk=pk) if pk else None

    def switch_off_previous(obj):
        # Keep "one active rule per ride type + city": the new active rule replaces the old one.
        if obj.is_active:
            FareRule.objects.filter(ride_type=obj.ride_type, city=obj.city, is_active=True).exclude(pk=obj.pk).update(is_active=False)

    return edit_record(request, FareRuleForm, rule, title="Edit fare rule" if rule else "New fare rule", back=_back("fares"),
                       audit_name="fare_rule", before_save=switch_off_previous,
                       intro="Fare = base + per km × distance + per minute × time + booking fee, at least the minimum, rounded to ₦50. "
                             "New prices apply to new estimates immediately.")


@staff_area("settings.edit")
def ride_type_edit(request, pk=None):
    obj = get_object_or_404(RideType, pk=pk) if pk else None
    return edit_record(request, RideTypeForm, obj, title=obj.name if obj else "New ride type", back=_back("ride-types"),
                       audit_name="ride_type")


@staff_area("settings.edit")
def zone_edit(request, pk=None):
    obj = get_object_or_404(ServiceZone, pk=pk) if pk else None
    return edit_record(request, ServiceZoneForm, obj, title=obj.name if obj else "New service zone", back=_back("zones"),
                       audit_name="service_zone",
                       intro="If a service has no active zones in a city, the whole city is allowed. Otherwise pickups and "
                             "drop-offs must be inside a zone.")


@staff_area("team")
def staff_edit(request, pk=None):
    obj = get_object_or_404(User, pk=pk, is_staff=True) if pk else None
    return edit_record(request, StaffMemberForm, obj, title=obj.full_name if obj else "Add staff member", back=_back("team"),
                       audit_name="staff",
                       intro="Staff sign in to this dashboard with their email and password. To remove access, suspend them under Users.")
