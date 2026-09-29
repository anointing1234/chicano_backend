"""
Numbers for the dashboards and reports. Every figure is a database query over real records —
nothing is estimated or hard-coded. Views stay thin: they pick a Period and call these.

    customer_panel(period, service)   Customers & Rides dashboard
    driver_panel(period, service)     Drivers & Fleet dashboard
    report(period, service, city)     Reports & analytics (also feeds the CSV export)

`service` is None (all), "car" or "bike". Money is kobo. "Previous" = the same-length period just before.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.customers.models import CustomerProfile
from apps.payments.models import Payout, PromoRedemption
from apps.providers.models import DocumentStatus, ProviderDocument, ProviderProfile, ProviderStatus
from apps.rides.models import PROVIDER_BUSY_STATUSES, Ride, RideStatus

from .access import Period, delta

User = get_user_model()


# =========================================================================== helpers
def _rides(service):
    qs = Ride.objects.all()
    return qs.filter(service=service) if service else qs


def _providers(service):
    qs = ProviderProfile.objects.all()
    return qs.filter(service=service) if service else qs


def daily(qs, field: str, period: Period, total: str | None = None) -> list[dict]:
    """One row per day in the period: {date, n, total}. Days without data are 0."""
    rows = (qs.filter(**{f"{field}__gte": period.start, f"{field}__lt": period.end})
            .annotate(day=TruncDate(field)).values("day")
            .annotate(n=Count("id"), total=Sum(total) if total else Count("id")))
    by_day = {r["day"]: r for r in rows}
    return [{"date": d, "n": by_day.get(d, {}).get("n", 0), "total": by_day.get(d, {}).get("total") or 0} for d in period.days]


def bars(rows: list[dict], key: str, fmt=lambda v: f"{v:,}", label_fmt: str = "%a") -> list[dict]:
    """Turn daily rows into bar-chart series (heights relative to the highest bar)."""
    top = max([r[key] for r in rows] + [1])
    many = len(rows) > 14
    out = []
    for r in rows:
        d = r["date"]
        lbl = str(d.day) if many else d.strftime(label_fmt)      # no "%-d": it crashes on Windows
        out.append({"label": lbl, "value": r[key], "display": fmt(r[key]), "height": round(r[key] / top * 100),
                    "title": f"{d:%a} {d.day} {d:%b}: {fmt(r[key])}"})
    return out


SERVICE_NAMES = {"car": "Cars", "bike": "Bikes"}


def breakdown(rows, label_key: str, value_key: str, fmt=lambda v: f"{v:,}", cls_key: str | None = None) -> list[dict]:
    """Rows for horizontal bars, largest first."""
    rows = sorted(rows, key=lambda r: r[value_key] or 0, reverse=True)
    top = max([r[value_key] or 0 for r in rows] + [1])
    return [{"label": r[label_key] or "—", "display": fmt(r[value_key] or 0), "pct": round((r[value_key] or 0) / top * 100),
             "cls": r.get(cls_key, "") if cls_key else ""} for r in rows]


def naira(kobo) -> str:
    return f"₦{(kobo or 0) / 100:,.0f}"


def _in(qs, field, start, end):
    return qs.filter(**{f"{field}__gte": start, f"{field}__lt": end})


# =========================================================================== Customers & Rides
def customer_panel(period: Period, service: str | None) -> dict:
    rides = _rides(service)
    now_rides = rides.filter(status__in=[RideStatus.SEARCHING, *PROVIDER_BUSY_STATUSES])
    cur = _in(rides, "requested_at", period.start, period.end)
    prev = _in(rides, "requested_at", period.prev_start, period.prev_end)
    done_cur = _in(rides.filter(status=RideStatus.COMPLETED), "completed_at", period.start, period.end)
    done_prev = _in(rides.filter(status=RideStatus.COMPLETED), "completed_at", period.prev_start, period.prev_end)
    money_cur = done_cur.aggregate(gross=Sum("total_amount"), promo=Sum("discount_amount"), commission=Sum("commission_amount"), tips=Sum("tip_amount"))
    money_prev = done_prev.aggregate(gross=Sum("total_amount"), promo=Sum("discount_amount"))
    cancelled_cur = cur.filter(status=RideStatus.CANCELLED).count()
    cancelled_prev = prev.filter(status=RideStatus.CANCELLED).count()

    customers = CustomerProfile.objects.all()
    new_cur = _in(customers, "created_at", period.start, period.end).count()
    new_prev = _in(customers, "created_at", period.prev_start, period.prev_end).count()
    active_cur = cur.values("customer").distinct().count()
    active_prev = prev.values("customer").distinct().count()

    return {
        "customers_total": customers.count(),
        "customers_suspended": User.objects.filter(customer_profile__isnull=False).exclude(status="active").count(),
        "unverified_phones": User.objects.filter(customer_profile__isnull=False, phone_verified_at__isnull=True).count(),
        "new_customers": new_cur, "new_customers_delta": delta(new_cur, new_prev),
        "active_customers": active_cur, "active_customers_delta": delta(active_cur, active_prev),
        "rides_requested": cur.count(), "rides_requested_delta": delta(cur.count(), prev.count()),
        "rides_now": now_rides.count(),
        "rides_completed": done_cur.count(), "rides_completed_delta": delta(done_cur.count(), done_prev.count()),
        "rides_cancelled": cancelled_cur, "rides_cancelled_delta": delta(cancelled_cur, cancelled_prev),
        "gross": money_cur["gross"] or 0, "gross_delta": delta(money_cur["gross"] or 0, money_prev["gross"] or 0),
        "commission": money_cur["commission"] or 0, "tips": money_cur["tips"] or 0,
        "promo_spend": money_cur["promo"] or 0, "promo_delta": delta(money_cur["promo"] or 0, money_prev["promo"] or 0),
        "payments_failed": rides.filter(payment_status="failed").count(),
        "chart_rides": bars(daily(rides.filter(status=RideStatus.COMPLETED), "completed_at", period), "n"),
        "chart_signups": bars(daily(customers, "created_at", period), "n"),
        "by_ride_type": breakdown(list(done_cur.values("ride_type__name").annotate(n=Count("id"))), "ride_type__name", "n"),
        "by_payment": breakdown(list(done_cur.values("payment_method").annotate(total=Sum("total_amount"))), "payment_method", "total", naira),
        "cancel_by": breakdown(list(cur.filter(status=RideStatus.CANCELLED).values("cancelled_by").annotate(n=Count("id"))), "cancelled_by", "n"),
    }


# =========================================================================== Drivers & Fleet
def driver_panel(period: Period, service: str | None) -> dict:
    providers = _providers(service)
    rides = _rides(service)
    soon = timezone.localdate() + timedelta(days=30)
    docs = ProviderDocument.objects.filter(provider__in=providers)
    done_cur = _in(rides.filter(status=RideStatus.COMPLETED), "completed_at", period.start, period.end)
    done_prev = _in(rides.filter(status=RideStatus.COMPLETED), "completed_at", period.prev_start, period.prev_end)
    apps_cur = _in(providers, "created_at", period.start, period.end).count()
    apps_prev = _in(providers, "created_at", period.prev_start, period.prev_end).count()
    approved = providers.filter(status=ProviderStatus.APPROVED)
    quality = approved.aggregate(rating=Avg("rating_avg"), received=Sum("offers_received"), accepted=Sum("offers_accepted"))
    payouts = Payout.objects.filter(provider__in=providers, status__in=["pending", "processing"])
    active_ids = done_cur.values("provider").distinct()
    top = (done_cur.values("provider", "provider__user__first_name", "provider__user__last_name", "provider__service")
           .annotate(n=Count("id"), gross=Sum("gross_amount")).order_by("-n")[:5])

    return {
        "providers_total": providers.count(),
        "approved": approved.count(),
        "online": providers.filter(is_online=True).count(),
        "awaiting_approval": providers.filter(status=ProviderStatus.UNDER_REVIEW).count(),
        "applications": apps_cur, "applications_delta": delta(apps_cur, apps_prev),
        "documents_pending": docs.filter(status=DocumentStatus.PENDING).count(),
        "documents_expiring": docs.filter(status=DocumentStatus.APPROVED, expires_at__lte=soon, expires_at__gte=timezone.localdate()).count(),
        "documents_expired": docs.filter(Q(status=DocumentStatus.EXPIRED) | Q(status=DocumentStatus.APPROVED, expires_at__lt=timezone.localdate())).count(),
        "active_rides": rides.filter(status__in=PROVIDER_BUSY_STATUSES).count(),
        "payouts_pending": payouts.count(), "payouts_pending_amount": payouts.aggregate(s=Sum("amount"))["s"] or 0,
        "trips": done_cur.count(), "trips_delta": delta(done_cur.count(), done_prev.count()),
        "active_providers": active_ids.count(),
        "avg_rating": quality["rating"],
        "avg_rating_display": f"★ {quality['rating']:.2f}" if quality["rating"] is not None else "—",
        "online_desc": f"Online right now, out of {approved.count():,} approved",
        "payouts_desc": f"{payouts.count():,} payout{'s' if payouts.count() != 1 else ''} not yet paid",
        "acceptance": round(100 * quality["accepted"] / quality["received"], 1) if quality["received"] else None,
        "earnings_gross": done_cur.aggregate(s=Sum("gross_amount"))["s"] or 0,
        "chart_trips": bars(daily(rides.filter(status=RideStatus.COMPLETED), "completed_at", period), "n"),
        "chart_applications": bars(daily(providers, "created_at", period), "n"),
        "by_status": breakdown([{"label": str(ProviderStatus(r["status"]).label), "n": r["n"]}
                                for r in providers.values("status").annotate(n=Count("id"))], "label", "n"),
        "top": [{"name": f"{t['provider__user__first_name']} {t['provider__user__last_name']}".strip(), "id": t["provider"],
                 "service": t["provider__service"], "trips": t["n"], "gross": t["gross"] or 0} for t in top],
    }


# =========================================================================== Reports
def report(period: Period, service: str | None, city: str | None) -> dict:
    rides = _rides(service)
    if city:
        rides = rides.filter(city=city)
    cur = _in(rides, "requested_at", period.start, period.end)
    prev = _in(rides, "requested_at", period.prev_start, period.prev_end)
    done = _in(rides.filter(status=RideStatus.COMPLETED), "completed_at", period.start, period.end)
    done_prev = _in(rides.filter(status=RideStatus.COMPLETED), "completed_at", period.prev_start, period.prev_end)
    agg = done.aggregate(gross=Sum("total_amount"), commission=Sum("commission_amount"), promo=Sum("discount_amount"),
                         tips=Sum("tip_amount"), refunds=Sum("refunded_amount"))
    agg_prev = done_prev.aggregate(gross=Sum("total_amount"), commission=Sum("commission_amount"), promo=Sum("discount_amount"))
    cancelled = cur.filter(status=RideStatus.CANCELLED).count()
    cancel_rate = round(100 * cancelled / cur.count(), 1) if cur.count() else None
    cancelled_prev = prev.filter(status=RideStatus.CANCELLED).count()
    cancel_rate_prev = round(100 * cancelled_prev / prev.count(), 1) if prev.count() else None

    # Customer growth + retention: returning = customers with 2+ completed trips in the period.
    per_customer = done.values("customer").annotate(n=Count("id"))
    riding = per_customer.count()
    returning = per_customer.filter(n__gte=2).count()
    customers = CustomerProfile.objects.all()
    new_customers = _in(customers, "created_at", period.start, period.end).count()

    # Driver/rider activity.
    per_provider = done.values("provider").annotate(n=Count("id"))
    active_providers = per_provider.count()

    daily_trips = daily(rides.filter(status=RideStatus.COMPLETED), "completed_at", period, total="total_amount")
    daily_signups = {r["date"]: r["n"] for r in daily(customers, "created_at", period)}
    daily_rows = [{"date": r["date"], "trips": r["n"], "gross": r["total"], "new_customers": daily_signups.get(r["date"], 0)} for r in daily_trips]

    promo_rows = (PromoRedemption.objects.filter(ride__in=done).values("promo__code")
                  .annotate(n=Count("id"), total=Sum("discount_amount")).order_by("-total"))

    return {
        "trips": done.count(), "trips_delta": delta(done.count(), done_prev.count()),
        "requested": cur.count(),
        "trips_desc": f"Out of {cur.count():,} rides requested",
        "cancel_rate_desc": (f"{cancelled:,} of {cur.count():,} bookings"
                             + (f" · {cancel_rate - cancel_rate_prev:+.1f} pts vs previous" if cancel_rate is not None and cancel_rate_prev is not None else "")),
        "gross": agg["gross"] or 0, "gross_delta": delta(agg["gross"] or 0, agg_prev["gross"] or 0),
        "commission": agg["commission"] or 0, "commission_delta": delta(agg["commission"] or 0, agg_prev["commission"] or 0),
        "promo": agg["promo"] or 0, "promo_delta": delta(agg["promo"] or 0, agg_prev["promo"] or 0),
        "tips": agg["tips"] or 0, "refunds": agg["refunds"] or 0,
        "avg_fare": round((agg["gross"] or 0) / done.count()) if done.count() else 0,
        "cancel_rate": cancel_rate, "cancel_rate_delta": (round(cancel_rate - cancel_rate_prev, 1) if cancel_rate is not None and cancel_rate_prev is not None else None),
        "new_customers": new_customers, "riding_customers": riding, "returning_customers": returning,
        "retention": round(100 * returning / riding, 1) if riding else None,
        "active_providers": active_providers,
        "trips_per_provider": round(done.count() / active_providers, 1) if active_providers else None,
        "chart_trips": bars(daily_trips, "n"),
        "chart_gross": bars(daily_trips, "total", naira),
        "by_service": breakdown([{**r, "cls": r["service"], "name": SERVICE_NAMES.get(r["service"], r["service"])}
                                 for r in done.values("service").annotate(total=Sum("total_amount"))],
                                "name", "total", naira, cls_key="cls"),
        "by_ride_type": breakdown(list(done.values("ride_type__name").annotate(n=Count("id"))), "ride_type__name", "n"),
        "by_city": breakdown(list(done.values("city").annotate(total=Sum("total_amount"))), "city", "total", naira),
        "promos": [{"code": p["promo__code"], "uses": p["n"], "total": p["total"] or 0} for p in promo_rows],
        "daily": daily_rows,
    }


def cities() -> list[str]:
    """Cities that exist in the data (rides, drivers, fares) — used for city filters."""
    from apps.pricing.models import FareRule
    names = set(Ride.objects.values_list("city", flat=True).distinct()) | set(ProviderProfile.objects.values_list("city", flat=True).distinct())
    names |= set(FareRule.objects.values_list("city", flat=True).distinct())
    return sorted(n for n in names if n)
