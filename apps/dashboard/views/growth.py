"""
Promo codes (Customers & Rides panel) and driver/rider incentives (Drivers & Fleet panel).
Both are paid for by Chicano Cruise. Create/edit pages use _crud.edit_record (audited).
"""
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone

from apps.payments.models import Incentive, PromoCode

from ..access import apply_sort, current_service, paginate, staff_area
from ..forms import IncentiveForm, PromoForm
from ._crud import edit_record


def _state_filter(qs, state, start="valid_from", end="valid_to"):
    now = timezone.now()
    if state == "live":
        return qs.filter(is_active=True, **{f"{start}__lte": now, f"{end}__gt": now})
    if state == "scheduled":
        return qs.filter(is_active=True, **{f"{start}__gt": now})
    if state == "ended":
        return qs.filter(Q(is_active=False) | Q(**{f"{end}__lte": now}))
    return qs


@staff_area("growth")
def promotions(request):
    service = current_service(request)
    qs = PromoCode.objects.annotate(redemptions_count=Count("redemptions"))
    if service:
        qs = qs.filter(Q(service=service) | Q(service__isnull=True) | Q(service=""))
    if request.GET.get("q"):
        qs = qs.filter(Q(code__icontains=request.GET["q"].strip()) | Q(description__icontains=request.GET["q"].strip()))
    qs = _state_filter(qs, request.GET.get("state", ""))
    qs, sort = apply_sort(request, qs, {"code": "code", "uses": "redemptions_count", "spent": "spent_amount", "ends": "valid_to", "created": "created_at"}, "-created")
    live = PromoCode.objects.filter(is_active=True, valid_from__lte=timezone.now(), valid_to__gt=timezone.now())
    return render(request, "dashboard/growth/promotions.html", {
        "page": paginate(request, qs), "sort": sort, "f": request.GET, "now": timezone.now(), "panel": "customer",
        "live_count": live.count(), "spent_total": PromoCode.objects.aggregate(s=Sum("spent_amount"))["s"] or 0})


@staff_area("growth")
def incentives(request):
    service = current_service(request)
    qs = Incentive.objects.annotate(award_count=Count("awards"))
    if service:
        qs = qs.filter(service=service)
    qs = _state_filter(qs, request.GET.get("state", ""), "starts_at", "ends_at")
    qs, sort = apply_sort(request, qs, {"name": "name", "ends": "ends_at", "reward": "reward_amount"}, "-ends")
    return render(request, "dashboard/growth/incentives.html", {
        "page": paginate(request, qs), "sort": sort, "f": request.GET, "now": timezone.now(), "panel": "driver"})


@staff_area("growth")
def promo_edit(request, pk=None):
    promo = get_object_or_404(PromoCode, pk=pk) if pk else None
    return edit_record(request, PromoForm, promo, title=f"Promo code {promo.code}" if promo else "New promo code",
                       back=reverse("dashboard:promotions"), audit_name="promo", panel="customer", noun="promo code",
                       crumb=("Promo codes", reverse("dashboard:promotions")),
                       intro="Customers enter the code in the app before booking. The discount is taken off the fare they pay.")


@staff_area("growth")
def incentive_edit(request, pk=None):
    incentive = get_object_or_404(Incentive, pk=pk) if pk else None
    return edit_record(request, IncentiveForm, incentive, title=incentive.name if incentive else "New incentive",
                       back=reverse("dashboard:incentives"), audit_name="incentive", panel="driver", noun="incentive",
                       crumb=("Driver incentives", reverse("dashboard:incentives")),
                       intro="A trip-count bonus. Drivers and riders see their progress in the app.")
