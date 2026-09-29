"""
Payments & refunds (Customers & Rides panel) — read-only records; refunds are issued from a ride page.

    ?tab=records   every Payment row (ride fares, tips, fees, top-ups, card refunds)
    ?tab=refunds   refunds to cards (Payment) and to wallets (WalletTransaction)
    ?tab=failed    rides whose card/wallet charge failed (customer sees "retry payment")
    ?tab=disputes  support tickets about fares & payments
"""
from django.db.models import Q, Sum
from django.shortcuts import render

from apps.payments.models import Payment, PaymentPurpose, WalletTransaction, WalletTxKind
from apps.rides.models import PaymentStatus, Ride
from apps.support.models import SupportTicket, TicketCategory

from ..access import RANGES, get_period, paginate, staff_area

TABS = ["records", "refunds", "failed", "disputes"]


def _search(qs, q, prefix=""):
    if not q:
        return qs
    s = q.strip()
    return qs.filter(Q(**{f"{prefix}phone__icontains": s.lstrip("0").replace(" ", "")}) | Q(**{f"{prefix}first_name__icontains": s})
                     | Q(**{f"{prefix}last_name__icontains": s}) | Q(**{f"{prefix}email__icontains": s}))


@staff_area("payments.view")
def payments(request):
    tab = request.GET.get("tab", "records")
    tab = tab if tab in TABS else "records"
    period = get_period(request, default="30d")
    g = request.GET
    in_period = {"created_at__gte": period.start, "created_at__lt": period.end}
    ctx = {"tab": tab, "period": period, "ranges": RANGES, "f": g, "panel": "customer",
           "purposes": PaymentPurpose.choices}

    period_payments = Payment.objects.filter(**in_period)
    ctx["summary"] = {
        "collected": period_payments.filter(status="succeeded").exclude(purpose=PaymentPurpose.REFUND).aggregate(s=Sum("amount"))["s"] or 0,
        "card_refunds": -(period_payments.filter(purpose=PaymentPurpose.REFUND, status="succeeded").aggregate(s=Sum("amount"))["s"] or 0),
        "wallet_refunds": WalletTransaction.objects.filter(kind=WalletTxKind.REFUND, **in_period).aggregate(s=Sum("amount"))["s"] or 0,
        "failed": period_payments.filter(status="failed").count(),
        "open_failed_rides": Ride.objects.filter(payment_status=PaymentStatus.FAILED).count(),
    }

    if tab == "records":
        qs = _search(Payment.objects.select_related("user", "ride").filter(**in_period), g.get("q"), "user__")
        for key, field in (("method", "method"), ("status", "status"), ("purpose", "purpose")):
            if g.get(key):
                qs = qs.filter(**{field: g[key]})
        if g.get("q") and not qs.exists():
            qs = Payment.objects.select_related("user", "ride").filter(gateway_reference__icontains=g["q"].strip(), **in_period)
        ctx["page"] = paginate(request, qs.order_by("-created_at"))
    elif tab == "refunds":
        card = [{"when": p.created_at, "user": p.user, "ride": p.ride, "amount": -p.amount, "channel": "Card", "note": p.gateway_reference}
                for p in _search(Payment.objects.select_related("user", "ride").filter(purpose=PaymentPurpose.REFUND, **in_period), g.get("q"), "user__")]
        wallet = [{"when": t.created_at, "user": t.wallet.user, "ride": t.ride, "amount": t.amount, "channel": "Wallet", "note": t.note}
                  for t in _search(WalletTransaction.objects.select_related("wallet__user", "ride").filter(kind=WalletTxKind.REFUND, **in_period),
                                   g.get("q"), "wallet__user__")]
        ctx["page"] = paginate(request, sorted(card + wallet, key=lambda r: r["when"], reverse=True))
    elif tab == "failed":
        qs = _search(Ride.objects.select_related("customer", "ride_type").filter(payment_status=PaymentStatus.FAILED), g.get("q"), "customer__")
        ctx["page"] = paginate(request, qs.order_by("-completed_at"))
    else:
        qs = _search(SupportTicket.objects.select_related("user", "ride", "assigned_to").filter(category=TicketCategory.FARE), g.get("q"), "user__")
        if g.get("status") in ("open", "pending", "resolved", "closed"):
            qs = qs.filter(status=g["status"])
        ctx["page"] = paginate(request, qs.order_by("-updated_at"))
    return render(request, "dashboard/payments.html", ctx)
