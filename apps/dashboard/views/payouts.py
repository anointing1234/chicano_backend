"""
Payouts & commission (Drivers & Fleet panel, finance).

    ?tab=payouts      pending → processing → paid / failed; run weekly payouts; mark paid (bank ref) / failed
    ?tab=commission   commission earned in the period by business line and ride type, and the
                      commission % set on each fare rule (edited in Settings › Fares by a super admin)
"""
from django.db.models import Count, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.payments.models import Payout, PayoutStatus
from apps.pricing.models import FareRule
from apps.rides.models import Ride, RideStatus
from apps.staff import services

from .. import metrics
from ..access import RANGES, back, current_service, get_period, paginate, run_action, staff_area


@staff_area("payouts")
def payout_list(request):
    service = current_service(request)
    tab = request.GET.get("tab", "payouts")
    period = get_period(request, default="30d")
    ctx = {"tab": tab, "period": period, "ranges": RANGES, "f": request.GET, "panel": "driver"}
    payouts = Payout.objects.select_related("provider__user", "processed_by")
    if service:
        payouts = payouts.filter(provider__service=service)
    totals = {r["status"]: r for r in payouts.values("status").annotate(n=Count("id"), s=Sum("amount"))}
    ctx["totals"] = [(k, str(label), totals.get(k, {}).get("n", 0), totals.get(k, {}).get("s") or 0) for k, label in PayoutStatus.choices]

    if tab == "commission":
        rides = Ride.objects.filter(status=RideStatus.COMPLETED, completed_at__gte=period.start, completed_at__lt=period.end)
        if service:
            rides = rides.filter(service=service)
        agg = rides.aggregate(commission=Sum("commission_amount"), gross=Sum("gross_amount"))
        ctx["commission"] = agg["commission"] or 0
        ctx["gross"] = agg["gross"] or 0
        ctx["effective_rate"] = round(100 * ctx["commission"] / ctx["gross"], 1) if ctx["gross"] else None
        ctx["by_type"] = metrics.breakdown(list(rides.values("ride_type__name").annotate(total=Sum("commission_amount"))),
                                           "ride_type__name", "total", metrics.naira)
        rules = FareRule.objects.select_related("ride_type").order_by("city", "ride_type__service", "ride_type__sort_order")
        ctx["rules"] = rules.filter(ride_type__service=service) if service else rules
        return render(request, "dashboard/payouts.html", ctx)

    status = request.GET.get("status", "pending")
    qs = payouts
    if status:
        qs = qs.filter(status=status)
    if request.GET.get("method"):
        qs = qs.filter(method=request.GET["method"])
    if request.GET.get("q"):
        s = request.GET["q"].strip()
        qs = qs.filter(Q(provider__user__phone__icontains=s.lstrip("0").replace(" ", "")) | Q(provider__user__first_name__icontains=s)
                       | Q(provider__user__last_name__icontains=s) | Q(reference__icontains=s))
    ctx.update(page=paginate(request, qs.order_by("-created_at")), status=status, statuses=PayoutStatus.choices)
    return render(request, "dashboard/payouts.html", ctx)


@require_POST
@staff_area("payouts")
def payout_run(request):
    created = []
    if run_action(request, lambda: created.extend(services.run_payouts(request)), "Weekly payouts created."):
        from django.contrib import messages
        total = sum(p.amount for p in created) / 100
        if created:
            messages.info(request, f"{len(created)} payout{'s' if len(created) != 1 else ''}, ₦{total:,.0f} in total. Send the transfers, then mark each one paid.")
        else:
            messages.info(request, "Nobody is owed ₦1,000 or more with bank details on file, so no payouts were created.")
    return redirect(f"{reverse('dashboard:payouts')}?status=pending")


@require_POST
@staff_area("payouts")
def payout_action(request, pk, action):
    if action not in ("paid", "failed"):
        raise Http404("Unknown action")
    payout = get_object_or_404(Payout.objects.select_related("provider__user"), pk=pk)
    if action == "paid":
        reference = request.POST.get("reference", "").strip()
        run_action(request, lambda: services.mark_payout_paid(request, payout, reference), "Payout marked paid. They've been notified.")
    else:
        reason = request.POST.get("reason", "").strip() or "Bank rejected the transfer."
        run_action(request, lambda: services.mark_payout_failed(request, payout, reason), "Payout marked failed. The money is back in their balance.")
    return back(request, "dashboard:payouts")
