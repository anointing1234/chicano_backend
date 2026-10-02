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
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.payments.models import Payout, PayoutStatus
from apps.pricing.models import FareRule
from apps.rides.models import Ride, RideStatus
from apps.staff import services

from .. import metrics
from ..access import RANGES, back, csv_response, current_service, get_period, paginate, run_action, staff_area


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

    # ---- the batch board (A08): the latest weekly run by default, ?batch=all for every payout
    svc = request.GET.get("svc") if request.GET.get("svc") in ("car", "bike") else None
    status, bank = request.GET.get("status", ""), request.GET.get("bank", "")
    latest = payouts.filter(method="scheduled").order_by("-created_at").first()
    batch_all = request.GET.get("batch") == "all" or not latest
    batch = payouts
    if not batch_all:
        day = timezone.localtime(latest.created_at).date()
        batch = payouts.filter(method="scheduled", created_at__date=day)
    qs = batch
    if svc:
        qs = qs.filter(provider__service=svc)
    if status:
        qs = qs.filter(status=status)
    if bank:
        qs = qs.filter(bank_name=bank)
    if request.GET.get("q"):
        s_ = request.GET["q"].strip()
        qs = qs.filter(Q(provider__user__phone__icontains=s_.lstrip("0").replace(" ", "")) | Q(provider__user__first_name__icontains=s_)
                       | Q(provider__user__last_name__icontains=s_) | Q(reference__icontains=s_))
    if request.GET.get("export") == "csv":
        return csv_response("chicano-payout-batch.csv", ["payout_id", "name", "service", "bank", "account_number", "account_name", "amount_naira", "status", "reference", "failure_reason"],
                            [[str(p.pk), p.provider.user.full_name, p.provider.service, p.bank_name, p.bank_account_number, p.bank_account_name,
                              f"{p.amount / 100:.2f}", p.status, p.reference, p.failure_reason] for p in qs.order_by("provider__user__first_name")])
    by = {r["provider__service"]: r["n"] for r in batch.values("provider__service").annotate(n=Count("id"))}
    counts = {r["status"]: r["n"] for r in batch.values("status").annotate(n=Count("id"))}
    n_batch = batch.count()
    failed = batch.filter(status="failed")
    reasons = list(failed.values("failure_reason").annotate(n=Count("id")).order_by("-n")[:1])
    from apps.payments.services import MIN_PAYOUT_AMOUNT, provider_balance
    eligible = sum(1 for p in {f.provider for f in failed.select_related("provider")}
                   if p.bank_account_number and provider_balance(p) >= MIN_PAYOUT_AMOUNT)
    page = paginate(request, qs.order_by("-created_at"))
    _annotate_rows(page.object_list)
    ctx.update(page=page, status=status, statuses=PayoutStatus.choices, svc=svc or "", bank=bank,
               banks=sorted(set(batch.values_list("bank_name", flat=True))), batch_all=batch_all, latest=latest,
               k={"total": metrics.short_naira(batch.aggregate(s=Sum("amount"))["s"] or 0),
                  "split": f"{by.get('car', 0):,} drivers · {by.get('bike', 0):,} riders",
                  "paid": counts.get("paid", 0), "paid_pct": f"{100 * counts.get('paid', 0) / n_batch:.1f}%" if n_batch else "—",
                  "failed": counts.get("failed", 0), "on_hold": counts.get("on_hold", 0),
                  "pending": counts.get("pending", 0) + counts.get("processing", 0),
                  "top_reason": (reasons[0]["failure_reason"] or "bank rejected").lower() if reasons else "", "eligible": eligible})
    return render(request, "dashboard/payouts.html", ctx)


def _annotate_rows(payouts):
    """Trips, gross and commission behind each payout: the ledger since that person's previous payout."""
    from apps.payments.models import LedgerKind, ProviderLedgerEntry
    for p in payouts:
        prev = Payout.objects.filter(provider=p.provider, created_at__lt=p.created_at).order_by("-created_at").first()
        entries = ProviderLedgerEntry.objects.filter(provider=p.provider, created_at__lte=p.created_at)
        if prev:
            entries = entries.filter(created_at__gt=prev.created_at)
        agg = entries.values("kind").annotate(s=Sum("amount"), n=Count("ride", distinct=True))
        by = {r["kind"]: r for r in agg}
        p.trips = (by.get(LedgerKind.FARE) or {}).get("n", 0)
        p.gross = sum((by.get(k) or {}).get("s") or 0 for k in (LedgerKind.FARE, LedgerKind.TIP, LedgerKind.CANCELLATION_FEE, LedgerKind.INCENTIVE))
        p.commission = -((by.get(LedgerKind.COMMISSION) or {}).get("s") or 0)


@require_POST
@staff_area("payouts")
def payout_bulk(request):
    """Bulk bar on Payouts: mark the ticked payouts paid or failed, or export them."""
    from django.contrib import messages
    ids = request.POST.getlist("ids")
    action = request.POST.get("action")
    chosen = Payout.objects.filter(pk__in=ids).select_related("provider__user")
    if not chosen.exists():
        messages.error(request, "Tick at least one payout first.")
        return back(request, "dashboard:payouts")
    if action == "export":
        return csv_response("chicano-payouts-selected.csv", ["payout_id", "name", "bank", "account_number", "account_name", "amount_naira", "status"],
                            [[str(p.pk), p.provider.user.full_name, p.bank_name, p.bank_account_number, p.bank_account_name, f"{p.amount / 100:.2f}", p.status] for p in chosen])
    if action not in ("paid", "failed"):
        raise Http404("Unknown action")
    done, skipped = 0, 0
    reason = request.POST.get("reason", "").strip() or "Bank rejected the transfer."
    for p in chosen:
        if p.status not in ("pending", "processing", "on_hold"):
            skipped += 1
            continue
        try:
            if action == "paid":
                services.mark_payout_paid(request, p, request.POST.get("reference", "").strip())
            else:
                services.mark_payout_failed(request, p, reason)
            done += 1
        except Exception:
            skipped += 1
    messages.success(request, f"{done} payout{'s' if done != 1 else ''} marked {action}." + (f" {skipped} skipped (already settled)." if skipped else ""))
    return back(request, "dashboard:payouts")


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
