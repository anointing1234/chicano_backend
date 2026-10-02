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


# =========================================================================== Super Admin boards (A01 Overview, A05 Fleet)
def mk_delta(cur, prev, *, bad_up: bool = False, unit: str = "%", absolute: bool = False, digits: int = 1):
    """
    Change chip for a KPI card: {"text": "+6.1%", "tone": "good|bad|neutral", "arrow": "arrow-up|arrow-down"}.
    unit="%"  -> percent change vs the previous period;  unit="pt" -> difference in percentage points;
    absolute=True -> difference as a count (e.g. "+22"). None when there's nothing to compare.
    """
    if cur is None or prev is None:
        return None
    if absolute or unit == "pt":
        diff = cur - prev
        if unit == "pt":
            diff = round(diff, digits)
            text = f"{'+' if diff >= 0 else '−'}{abs(diff):.{digits}f} pt"
        else:
            text = f"{'+' if diff >= 0 else '−'}{abs(diff):,}"
    else:
        if not prev:
            return None
        diff = round((cur - prev) / prev * 100, digits)
        text = f"{'+' if diff >= 0 else '−'}{abs(diff):.{digits}f}%".replace(".0%", "%")
    if diff == 0:
        tone = "neutral"
    else:
        tone = "bad" if (diff > 0) == bad_up else "good"
    return {"text": text, "tone": tone, "arrow": "arrow-up" if diff >= 0 else "arrow-down"}


def _split(qs, field="service"):
    rows = dict(qs.values_list(field).annotate(n=Count("id")).values_list(field, "n"))
    return rows.get("car", 0), rows.get("bike", 0)


def _rate(cancelled, completed):
    total = cancelled + completed
    return round(100 * cancelled / total, 1) if total else None


HOURS = list(range(6, 21))          # 6a … 8p, like the board


def hourly(period: Period, service: str | None, day=None) -> list[dict]:
    """Completed trips per hour of day (6a–8p), cars vs bikes. `day` = one date, else the whole period."""
    from django.db.models.functions import ExtractHour
    qs = Ride.objects.filter(status=RideStatus.COMPLETED)
    if service:
        qs = qs.filter(service=service)
    if day is not None:
        tz = timezone.get_current_timezone()
        from datetime import datetime, time as dtime
        start = datetime.combine(day, dtime.min, tzinfo=tz)
        qs = qs.filter(completed_at__gte=start, completed_at__lt=start + timedelta(days=1))
    else:
        qs = _in(qs, "completed_at", period.start, period.end)
    rows = qs.annotate(h=ExtractHour("completed_at")).values("h", "service").annotate(n=Count("id"))
    grid = {(r["h"], r["service"]): r["n"] for r in rows}
    out = []
    for h in HOURS:
        label = f"{h % 12 or 12}{'a' if h < 12 else 'p'}"
        out.append({"hour": h, "label": label, "car": grid.get((h, "car"), 0), "bike": grid.get((h, "bike"), 0)})
    return out


def overview_board(period: Period, service: str | None) -> dict:
    """Everything on the Overview board (A01): KPIs, needs-attention queue. Live numbers are 'right now'."""
    from apps.support.models import SOSAlert, SupportTicket
    now = timezone.now()
    rides = _rides(service)
    providers = _providers(service)

    # right now
    recent = Ride.objects.filter(Q(status__in=[RideStatus.SEARCHING, *PROVIDER_BUSY_STATUSES]) | Q(requested_at__gte=now - timedelta(minutes=30)))
    recent_y = Ride.objects.filter(requested_at__gte=now - timedelta(days=1, minutes=30), requested_at__lt=now - timedelta(days=1))
    if service:
        recent, recent_y = recent.filter(service=service), recent_y.filter(service=service)
    users_car = recent.filter(service="car").values("customer").distinct().count()
    users_bike = recent.filter(service="bike").values("customer").distinct().count()
    users_now = recent.values("customer").distinct().count()
    online = providers.filter(is_online=True, status=ProviderStatus.APPROVED)
    on_car, on_bike = _split(online)
    active = rides.filter(status__in=[RideStatus.SEARCHING, *PROVIDER_BUSY_STATUSES])
    act_car, act_bike = _split(active)

    # the period
    done = _in(rides.filter(status=RideStatus.COMPLETED), "completed_at", period.start, period.end)
    done_prev = _in(rides.filter(status=RideStatus.COMPLETED), "completed_at", period.prev_start, period.prev_end)
    done_car, done_bike = _split(done)
    canc = _in(rides.filter(status=RideStatus.CANCELLED), "requested_at", period.start, period.end)
    canc_prev = _in(rides.filter(status=RideStatus.CANCELLED), "requested_at", period.prev_start, period.prev_end)
    c_car, c_bike = _split(canc)
    rate, rate_prev = _rate(canc.count(), done.count()), _rate(canc_prev.count(), done_prev.count())
    money = dict(done.values_list("service").annotate(s=Sum("total_amount")).values_list("service", "s"))
    revenue = sum(v or 0 for v in money.values())
    revenue_prev = done_prev.aggregate(s=Sum("total_amount"))["s"] or 0

    pending = providers.filter(status__in=[ProviderStatus.UNDER_REVIEW, ProviderStatus.DOCUMENTS_PENDING])
    pend_car, pend_bike = _split(pending)
    new_apps = _in(providers, "created_at", period.start, period.end).count()
    tickets = SupportTicket.objects.filter(status__in=["open", "pending"])
    over_sla = tickets.filter(created_at__lt=now - timedelta(hours=24))
    new_tickets = _in(SupportTicket.objects.all(), "created_at", period.start, period.end).count()

    # needs attention (only what has a count)
    attention = []
    sos = SOSAlert.objects.exclude(status="resolved")
    if service:
        sos = sos.filter(ride__service=service)
    if sos.exists():
        n = sos.count()
        attention.append({"tone": "bad", "icon": "alert", "title": f"{n} open SOS alert{'s' if n != 1 else ''}",
                          "sub": "Someone pressed SOS. Respond first.", "url": "support", "query": "?tab=sos", "link": "Respond", "count": n})
    if over_sla.exists():
        oldest = over_sla.order_by("created_at").first()
        hours = int((now - oldest.created_at).total_seconds() // 3600)
        n = over_sla.count()
        attention.append({"tone": "bad", "icon": "alert", "title": f"{n} ticket{'s' if n != 1 else ''} past 24 h SLA",
                          "sub": f"Oldest: {oldest.subject[:22].lower()}, {hours} h", "url": "support", "query": "", "link": "Open", "count": n})
    failed = rides.filter(payment_status="failed")
    if failed.exists():
        n = failed.count()
        amount = failed.aggregate(s=Sum("total_amount"))["s"] or 0
        attention.append({"tone": "amber", "icon": "cash", "title": f"{n} payment{'s' if n != 1 else ''} failed",
                          "sub": f"{naira(amount)} total · retry or collect", "url": "payments", "query": "?tab=failed", "link": "Review", "count": n})
    docs = ProviderDocument.objects.filter(status=DocumentStatus.PENDING)
    if service:
        docs = docs.filter(provider__service=service)
    if docs.exists():
        d_car, d_bike = _split(docs, "provider__service")
        n = docs.count()
        attention.append({"tone": "amber", "icon": "doc", "title": f"{n} document{'s' if n != 1 else ''} to review",
                          "sub": f"drivers {d_car} · riders {d_bike}", "url": "documents", "query": "", "link": "Review", "count": n})
    waiting = rides.filter(status=RideStatus.SEARCHING, needs_manual_dispatch=True)
    if waiting.exists():
        w_car, w_bike = _split(waiting)
        longest = int((now - waiting.order_by("requested_at").first().requested_at).total_seconds() // 60)
        n = waiting.count()
        parts = ", ".join(p for p in [f"{w_car} car{'s' if w_car != 1 else ''}" if w_car else "", f"{w_bike} bike{'s' if w_bike != 1 else ''}" if w_bike else ""] if p)
        attention.append({"tone": "bad", "icon": "radar", "title": f"{n} unassigned request{'s' if n != 1 else ''}",
                          "sub": f"{parts} · longest {longest} min", "url": "dispatch", "query": "", "link": "Dispatch", "count": n})
    failed_payouts = Payout.objects.filter(status="failed")
    if service:
        failed_payouts = failed_payouts.filter(provider__service=service)
    if failed_payouts.exists():
        n = failed_payouts.count()
        attention.append({"tone": "amber", "icon": "bank", "title": f"{n} payout{'s' if n != 1 else ''} failed",
                          "sub": "Bank details to confirm", "url": "payouts", "query": "?status=failed", "link": "Review", "count": n})

    today = period.key == "today"
    return {
        "users_now": users_now, "users_desc": f"cars {users_car:,} · bikes {users_bike:,}",
        "users_delta": mk_delta(users_now, recent_y.values("customer").distinct().count()),
        "online": online.count(), "online_desc": f"drivers {on_car:,} · riders {on_bike:,}",
        "active": active.count(), "active_desc": f"cars {act_car:,} · bikes {act_bike:,}",
        "completed_title": "Completed today" if today else "Completed",
        "completed": done.count(), "completed_desc": f"cars {done_car:,} · bikes {done_bike:,}",
        "completed_delta": mk_delta(done.count(), done_prev.count()),
        "cancel_rate": f"{rate:.1f}%" if rate is not None else "—",
        "cancel_desc": f"cars {_rate(c_car, done_car) or 0:.1f}% · bikes {_rate(c_bike, done_bike) or 0:.1f}%",
        "cancel_delta": mk_delta(rate, rate_prev, unit="pt", bad_up=True) if rate is not None and rate_prev is not None else None,
        "revenue_title": "Revenue today" if today else "Revenue",
        "revenue": short_naira(revenue), "revenue_desc": f"cars {short_naira(money.get('car') or 0)} · bikes {short_naira(money.get('bike') or 0)}",
        "revenue_delta": mk_delta(revenue, revenue_prev),
        "pending": pending.count(), "pending_desc": f"drivers {pend_car:,} · riders {pend_bike:,}",
        "pending_delta": mk_delta(new_apps, 0, absolute=True, bad_up=True) if new_apps else None,
        "tickets": tickets.count(), "tickets_desc": f"{over_sla.count():,} over 24 h SLA",
        "tickets_delta": mk_delta(new_tickets, 0, absolute=True, bad_up=True) if new_tickets else None,
        "attention": attention, "attention_total": sum(a["count"] for a in attention),
    }


def short_naira(kobo) -> str:
    """₦47.5M / ₦684k / ₦7,065 — the compact money format on the boards."""
    n = (kobo or 0) / 100
    if n >= 1_000_000:
        return f"₦{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 100_000:
        return f"₦{n / 1000:.0f}k"
    return f"₦{n:,.0f}"


def fleet_board(period: Period, service: str | None, pipe: str | None) -> dict:
    """Drivers & riders board (A05): supply KPIs, onboarding pipeline (last 30 days), expiring documents."""
    now = timezone.now()
    providers = _providers(service)
    approved = providers.filter(status=ProviderStatus.APPROVED)
    online = approved.filter(is_online=True)
    appr_car, appr_bike = _split(approved)
    on_car, on_bike = _split(online)
    pending = providers.filter(status__in=[ProviderStatus.UNDER_REVIEW, ProviderStatus.DOCUMENTS_PENDING])
    pend_car, pend_bike = _split(pending)
    new_apps = _in(providers, "created_at", period.start, period.end).count()

    today = timezone.localdate()
    docs = ProviderDocument.objects.filter(provider__in=providers)
    expired = docs.filter(Q(status=DocumentStatus.EXPIRED) | Q(status=DocumentStatus.APPROVED, expires_at__lt=today))
    rejected = docs.filter(status=DocumentStatus.REJECTED)
    from apps.providers.models import Vehicle
    vehicles_waiting = Vehicle.objects.filter(provider__in=providers, status="pending", is_active=True)
    issues = expired.count() + rejected.count() + vehicles_waiting.count()
    soon7 = docs.filter(status=DocumentStatus.APPROVED, expires_at__gte=today, expires_at__lte=today + timedelta(days=7))
    new_issues = _in(rejected, "reviewed_at", period.start, period.end).count()

    rides = _rides(service)
    done = _in(rides.filter(status=RideStatus.COMPLETED), "completed_at", period.start, period.end)
    done_prev = _in(rides.filter(status=RideStatus.COMPLETED), "completed_at", period.prev_start, period.prev_end)
    earn = {s: (g or 0) + (t or 0) - (c or 0) for s, g, t, c in done.values_list("service").annotate(
        g=Sum("total_amount"), t=Sum("tip_amount"), c=Sum("commission_amount")).values_list("service", "g", "t", "c")}
    earn_prev = done_prev.aggregate(g=Sum("total_amount"), c=Sum("commission_amount"))
    earned, earned_prev = sum(earn.values()), (earn_prev["g"] or 0) - (earn_prev["c"] or 0)

    def acceptance(svc):
        q = ProviderProfile.objects.filter(service=svc, status=ProviderStatus.APPROVED).aggregate(r=Sum("offers_received"), a=Sum("offers_accepted"))
        return round(100 * q["a"] / q["r"], 1) if q["r"] else None
    acc_car, acc_bike = acceptance("car"), acceptance("bike")
    TARGET = 85
    payouts = Payout.objects.filter(provider__in=providers, status__in=["pending", "processing"])

    # onboarding pipeline: applications from the last 30 days, how far they got
    pipe_qs = ProviderProfile.objects.filter(created_at__gte=now - timedelta(days=30))
    if pipe in ("car", "bike"):
        pipe_qs = pipe_qs.filter(service=pipe)
    applied = pipe_qs.count()
    with_docs = pipe_qs.filter(documents__isnull=False).distinct().count()
    checking = pipe_qs.filter(status__in=[ProviderStatus.UNDER_REVIEW, ProviderStatus.APPROVED]).count()
    inspect = pipe_qs.filter(status__in=[ProviderStatus.UNDER_REVIEW, ProviderStatus.APPROVED], vehicles__isnull=False).distinct().count()
    appr = pipe_qs.filter(status=ProviderStatus.APPROVED).count()
    stages = [("Applied", applied), ("Documents", with_docs), ("Background check", checking), ("Inspection", inspect), ("Approved", appr)]
    top = max(applied, 1)
    pipeline = [{"label": lbl, "n": n, "pct": round(100 * n / top)} for lbl, n in stages]

    expiring = (docs.filter(status=DocumentStatus.APPROVED, expires_at__gte=today, expires_at__lte=today + timedelta(days=30))
                .select_related("provider__user").order_by("expires_at"))
    rows = []
    for d in expiring[:5]:
        v = d.provider.vehicles.filter(is_active=True).first()
        rows.append({"doc": d, "days": (d.expires_at - today).days, "vehicle": v})

    def pct(v):
        return f"{v:.1f}%" if v is not None else "—"
    return {
        "on_car": on_car, "on_car_desc": f"of {appr_car:,} approved", "on_bike": on_bike, "on_bike_desc": f"of {appr_bike:,} approved",
        "pending": pending.count(), "pending_desc": f"drivers {pend_car:,} · riders {pend_bike:,}",
        "pending_delta": mk_delta(new_apps, 0, absolute=True, bad_up=True) if new_apps else None,
        "issues": issues, "issues_desc": f"{soon7.count():,} expiring in 7 days",
        "issues_delta": mk_delta(new_issues, 0, absolute=True, bad_up=True) if new_issues else None,
        "earn_title": "Earnings today" if period.key == "today" else "Earnings",
        "earned": short_naira(earned), "earned_desc": f"drivers {short_naira(earn.get('car', 0))} · riders {short_naira(earn.get('bike', 0))}",
        "earned_delta": mk_delta(earned, earned_prev),
        "acc_car": pct(acc_car), "acc_car_tone": acc_car is not None and acc_car >= TARGET,
        "acc_bike": pct(acc_bike), "acc_bike_tone": acc_bike is not None and acc_bike >= TARGET, "target": TARGET,
        "target_desc": f"target {TARGET}%",
        "acc_car_d": mk_delta(acc_car, TARGET, unit="pt") if acc_car is not None else None,
        "acc_bike_d": mk_delta(acc_bike, TARGET, unit="pt") if acc_bike is not None else None,
        "payouts": short_naira(payouts.aggregate(s=Sum("amount"))["s"] or 0), "payouts_desc": "weekly run Mon 06:00",
        "pipeline": pipeline, "pipeline_applied": applied,
        "expiring": rows, "expiring_total": expiring.count(),
    }
