"""
Small helpers every dashboard view uses.

    @staff_area("payouts")        login + role check (roles live in apps/core/roles.py)
    current_service(request)      the All / Cars / Bikes switch, remembered in the session
    paginate(request, queryset)   Django Paginator, 25 per page, ?page=N
    run_action(request, fn, msg)  call a service, show a green message or the rule's error in red
    get_period(request)           ?range=today|7d|30d|custom&from=&to= -> Period (with the previous period)
    apply_sort(request, qs, ...)  ?sort=field / -field with a whitelist (server-side sorting)
    csv_response(name, header, rows)  a CSV download
"""
import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import wraps
from urllib.parse import urlencode

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.core.exceptions import ApiError
from apps.core.roles import has_area, is_staff_member

SESSION_SERVICE_KEY = "dashboard_service"


def staff_area(area: str):
    """
    Decorator for dashboard views.
      * not logged in as active staff  -> redirect to the login page (then back here)
      * logged in but role not allowed -> 403 page
    """
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not is_staff_member(request.user):
                return redirect(f"{reverse('dashboard:login')}?{urlencode({'next': request.get_full_path()})}")
            if not has_area(request.user, area):
                return render(request, "dashboard/403.html", {"area": area}, status=403)
            return view(request, *args, **kwargs)
        return wrapper
    return decorator


def current_service(request) -> str | None:
    """
    ?service=all|car|bike on any dashboard URL switches the business line and is remembered.
    Returns None (all), "car" (drivers / User app · Cars) or "bike" (riders / User app · Bikes).
    """
    value = request.GET.get("service")
    if value in ("all", "car", "bike"):
        request.session[SESSION_SERVICE_KEY] = value
    value = request.session.get(SESSION_SERVICE_KEY, "all")
    return None if value == "all" else value


PAGE_SIZES = (10, 25, 50, 100)


def paginate(request, queryset, per_page: int = 25):
    """
    Return a Django Page for ?page=N (invalid numbers fall back to the last/first page).
    ?per_page= picks the rows per page from PAGE_SIZES (anything else uses the default).
    """
    try:
        size = int(request.GET.get("per_page", per_page))
    except (TypeError, ValueError):
        size = per_page
    if size not in PAGE_SIZES:
        size = per_page
    page = Paginator(queryset, size).get_page(request.GET.get("page"))
    page.per_page = size
    page.page_sizes = PAGE_SIZES
    return page


def run_action(request, fn, success: str) -> bool:
    """
    Run a service call. Business-rule errors (ApiError/Conflict) become a red message with the
    same text the API would return; success shows a green message. Returns True on success.
    """
    try:
        fn()
    except ApiError as exc:
        messages.error(request, str(exc.detail))
        return False
    messages.success(request, success)
    return True


def is_fragment(request) -> bool:
    """True when dashboard.js asks for just a piece of the page (live refresh)."""
    return request.headers.get("X-Fragment") == "1"


def back(request, default: str, **kwargs):
    """Redirect to the form's hidden `next` field if it's a safe local URL, else to `default`."""
    from django.utils.http import url_has_allowed_host_and_scheme
    next_url = request.POST.get("next") or request.GET.get("next")
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect(default, **kwargs)


# =========================================================================== date ranges
RANGES = [("today", "Today"), ("7d", "Last 7 days"), ("30d", "Last 30 days"), ("custom", "Custom range")]


@dataclass
class Period:
    """A date range in Lagos time. `prev_start`/`prev_end` is the same length immediately before it."""
    key: str
    label: str
    start: datetime
    end: datetime              # exclusive
    prev_start: datetime
    prev_end: datetime
    date_from: date
    date_to: date              # inclusive, for the date inputs

    @property
    def days(self) -> list[date]:
        return [self.date_from + timedelta(days=i) for i in range((self.date_to - self.date_from).days + 1)]


def get_period(request, default: str = "7d") -> Period:
    """Read ?range= (and ?from=&to= for custom). Invalid input falls back to the default."""
    key = request.GET.get("range", default)
    today = timezone.localdate()
    if key == "today":
        d_from, d_to = today, today
    elif key == "30d":
        d_from, d_to = today - timedelta(days=29), today
    elif key == "custom":
        d_from = parse_date(request.GET.get("from", "") or "") or today - timedelta(days=6)
        d_to = parse_date(request.GET.get("to", "") or "") or today
        if d_to < d_from:
            d_from, d_to = d_to, d_from
        d_to = min(d_to, today)
        d_from = max(d_from, d_to - timedelta(days=366))      # keep reports to at most a year
    else:
        key, d_from, d_to = "7d", today - timedelta(days=6), today
    tz = timezone.get_current_timezone()
    start = datetime.combine(d_from, time.min, tzinfo=tz)
    end = datetime.combine(d_to + timedelta(days=1), time.min, tzinfo=tz)
    length = end - start
    label = dict(RANGES)[key] if key != "custom" else f"{d_from.day} {d_from:%b} – {d_to.day} {d_to:%b %Y}"      # no "%-d": it crashes on Windows
    return Period(key, label, start, end, start - length, start, d_from, d_to)


def delta(current, previous):
    """Percent change for a stat card, or None when there's nothing to compare against."""
    if not previous:
        return None
    return round((current - previous) / previous * 100)


# =========================================================================== sorting
def apply_sort(request, qs, allowed: dict[str, str], default: str):
    """
    ?sort=<key> or ?sort=-<key>. `allowed` maps public keys to ORM fields, e.g. {"joined": "date_joined"}.
    Unknown keys fall back to `default`. Returns (queryset, current_sort_key).
    """
    raw = request.GET.get("sort") or default
    key = raw.lstrip("-")
    if key not in allowed:
        raw, key = default, default.lstrip("-")
    field = allowed[key]
    return qs.order_by(("-" if raw.startswith("-") else "") + field, "-pk"), raw


# =========================================================================== CSV
def csv_response(filename: str, header: list[str], rows) -> HttpResponse:
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("\ufeff")                     # so Excel opens UTF-8 (₦, names) correctly
    writer = csv.writer(response)
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return response
