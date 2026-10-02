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


def _promo_ctx(request) -> dict:
    """List + KPIs for the Promotions board (A04)."""
    from datetime import timedelta

    from apps.customers.models import CustomerProfile
    from apps.rides.models import Ride

    from ..metrics import mk_delta, short_naira
    service = current_service(request)
    qs = PromoCode.objects.annotate(redemptions_count=Count("redemptions"))
    if service:
        qs = qs.filter(Q(service=service) | Q(service__isnull=True) | Q(service=""))
    if request.GET.get("q"):
        qs = qs.filter(Q(code__icontains=request.GET["q"].strip()) | Q(description__icontains=request.GET["q"].strip()))
    state = request.GET.get("state", "")
    if state == "open":                       # "Active + scheduled"
        qs = qs.filter(is_active=True, valid_to__gt=timezone.now())
    else:
        qs = _state_filter(qs, state)
    kind = request.GET.get("type", "")
    if kind in ("percent", "flat"):
        qs = qs.filter(discount_type=kind)
    elif kind == "first_ride":
        qs = qs.filter(first_ride_only=True)
    qs, sort = apply_sort(request, qs, {"code": "code", "uses": "redemptions_count", "spent": "spent_amount", "ends": "valid_to", "created": "created_at"}, "-created")

    now = timezone.localtime()
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_month = (month - timedelta(days=1)).replace(day=1)
    rides = Ride.objects.filter(status="completed")
    if service:
        rides = rides.filter(service=service)
    cur, prev = rides.filter(completed_at__gte=month), rides.filter(completed_at__gte=prev_month, completed_at__lt=month)
    promo_cur, promo_prev = cur.filter(discount_amount__gt=0), prev.filter(discount_amount__gt=0)
    spend_cur = promo_cur.aggregate(s=Sum("discount_amount"))["s"] or 0
    spend_prev = promo_prev.aggregate(s=Sum("discount_amount"))["s"] or 0
    live = PromoCode.objects.filter(is_active=True, valid_from__lte=timezone.now(), valid_to__gt=timezone.now())
    budget = live.aggregate(s=Sum("budget_amount"))["s"] or 0
    refs = CustomerProfile.objects.filter(referred_by__isnull=False)
    ref_cur, ref_prev = refs.filter(created_at__gte=month).count(), refs.filter(created_at__gte=prev_month, created_at__lt=month).count()
    share = round(100 * promo_cur.count() / cur.count(), 1) if cur.count() else 0
    return {"page": paginate(request, qs), "sort": sort, "f": request.GET, "now": timezone.now(),
            "k": {"trips": promo_cur.count(), "trips_delta": mk_delta(promo_cur.count(), promo_prev.count()), "share": f"{share:g}% of trips",
                  "spend": short_naira(spend_cur), "spend_delta": mk_delta(spend_cur, spend_prev, bad_up=True),
                  "spend_desc": f"of {short_naira(budget)} budget" if budget else "no budget caps set",
                  "refs": ref_cur, "refs_delta": mk_delta(ref_cur, ref_prev)}}


@staff_area("growth")
def promotions(request):
    return render(request, "dashboard/growth/promotions.html", _promo_ctx(request))


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
    """
    New / edit promo code in the drawer on the Promotions board (A04).
    "Save draft" keeps it switched off (not visible to users); "Publish" switches it on.
    """
    from django.contrib import messages
    from django.db import transaction
    from django.shortcuts import redirect

    from apps.core.audit import log_action
    promo = get_object_or_404(PromoCode, pk=pk) if pk else None
    data = None
    if request.method == "POST":
        data = request.POST.copy()
        data["is_active"] = "on" if request.POST.get("publish") == "1" else ""
        data["code"] = (data.get("code") or "").strip().upper()
    initial = {}
    if not promo:
        from datetime import timedelta
        start = timezone.localtime().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        initial = {"valid_from": start, "valid_to": start + timedelta(days=30), "per_user_limit": 1, "discount_type": "flat"}
    form = PromoForm(data, instance=promo, initial=initial)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            obj = form.save()
        log_action(request, f"promo.{'update' if promo else 'create'}", obj, {"changed": form.changed_data, "published": obj.is_active})
        messages.success(request, f"{obj.code} {'published' if obj.is_active else 'saved as a draft'}.")
        return redirect("dashboard:promotions")
    if request.method == "POST":
        messages.error(request, "Please correct the highlighted fields.")
    ctx = _promo_ctx(request)
    ctx.update(form=form, promo=promo, drawer=True)
    return render(request, "dashboard/growth/promotions.html", ctx)


@staff_area("growth")
def incentive_edit(request, pk=None):
    incentive = get_object_or_404(Incentive, pk=pk) if pk else None
    return edit_record(request, IncentiveForm, incentive, title=incentive.name if incentive else "New incentive",
                       back=reverse("dashboard:incentives"), audit_name="incentive", panel="driver", noun="incentive",
                       crumb=("Driver incentives", reverse("dashboard:incentives")),
                       intro="A trip-count bonus. Drivers and riders see their progress in the app.")
