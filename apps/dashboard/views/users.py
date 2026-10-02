"""
Customers (Customers & Rides panel) and All accounts (Administration).

Both lists read the one combined users table. "Manage customers" shows people with a customer
profile; "All accounts" shows everyone (customers, drivers, riders, staff) with a role filter.
Detail page: profile, wallet, rides, payments, ratings, support tickets; suspend / ban / reinstate.
"""
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Avg, Count, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import UserStatus
from apps.core.audit import log_action
from apps.core.models import AuditLog
from apps.core.roles import has_area
from apps.rides.models import Rating, Ride
from apps.staff import services

from ..access import apply_sort, back, csv_response, paginate, run_action, staff_area

User = get_user_model()

SORTS = {"name": "first_name", "joined": "date_joined", "status": "status", "trips": "customer_profile__total_trips"}


def _list(request, customers_only: bool):
    qs = User.objects.select_related("customer_profile", "provider_profile", "wallet")
    role, status, verified, q = (request.GET.get(k, "").strip() for k in ("role", "status", "verified", "q"))
    if customers_only:
        qs = qs.filter(customer_profile__isnull=False)
    # Roles come from which profile rows exist (see accounts.User.roles).
    elif role == "customer":
        qs = qs.filter(customer_profile__isnull=False)
    elif role == "driver":
        qs = qs.filter(provider_profile__service="car")
    elif role == "rider":
        qs = qs.filter(provider_profile__service="bike")
    elif role == "staff":
        qs = qs.filter(is_staff=True)
    if status:
        qs = qs.filter(status=status)
    if verified == "yes":
        qs = qs.filter(phone_verified_at__isnull=False)
    elif verified == "no":
        qs = qs.filter(phone_verified_at__isnull=True)
    if q:
        qs = qs.filter(Q(phone__icontains=q.lstrip("0").replace(" ", "")) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
                       | Q(email__icontains=q))
    uses, joined = request.GET.get("uses", ""), request.GET.get("joined", "")
    if customers_only and uses in ("car", "bike", "both"):
        car_ids = Ride.objects.filter(service="car").values("customer_id")
        bike_ids = Ride.objects.filter(service="bike").values("customer_id")
        qs = {"car": qs.filter(pk__in=car_ids), "bike": qs.filter(pk__in=bike_ids),
              "both": qs.filter(pk__in=car_ids).filter(pk__in=bike_ids)}[uses]
    if joined in JOINED:
        qs = qs.filter(date_joined__gte=timezone.now() - timedelta(days=JOINED[joined][1]))
    qs, sort = apply_sort(request, qs, SORTS, "-joined")
    if request.GET.get("export") == "csv":
        return _export(qs, "chicano-users.csv")
    ctx = {"customers_only": customers_only, "page": paginate(request, qs), "sort": sort,
           "role": role, "status": status, "verified": verified, "q": q, "uses": uses, "joined": joined,
           "joined_choices": [(k, v[0]) for k, v in JOINED.items()], "statuses": UserStatus.choices}
    if request.GET.get("user"):
        ctx["drawer"], ctx["now"] = _drawer(request.GET["user"]), timezone.now()
    return render(request, "dashboard/users/list.html", ctx)


JOINED = {"7d": ("Last 7 days", 7), "30d": ("Last 30 days", 30), "90d": ("Last 90 days", 90), "365d": ("Last 12 months", 365)}


def _export(qs, filename):
    return csv_response(filename, ["name", "email", "phone", "status", "phone_verified", "trips", "joined"], [
        [u.full_name, u.email or "", u.phone, u.status, "yes" if u.phone_verified_at else "no",
         getattr(getattr(u, "customer_profile", None), "total_trips", ""), u.date_joined.date().isoformat()] for u in qs])


def _drawer(pk) -> dict | None:
    """User detail drawer (A02): badges, trips by service, lifetime spend, recent activity, last audit entry."""
    try:
        person = User.objects.select_related("customer_profile", "wallet").get(pk=pk)
    except (User.DoesNotExist, ValueError, Exception):
        return None
    rides = Ride.objects.filter(customer=person)
    by_service = dict(rides.filter(status="completed").values_list("service").annotate(n=Count("id")).values_list("service", "n"))
    spend = rides.filter(status="completed").aggregate(s=Sum("total_amount"), t=Sum("tip_amount"))
    methods = sorted(set(rides.values_list("payment_method", flat=True)))
    last = rides.order_by("-requested_at").first()
    activity = []
    for r in rides.select_related("provider__user").order_by("-requested_at")[:4]:
        ref = "TR-" + str(r.pk).replace("-", "")[:6].upper()
        who = f" · {r.provider.user.first_name} {r.provider.user.last_name[:1]}." if r.provider else ""
        if r.status == "completed":
            rated = r.ratings.filter(direction="customer_to_provider").first()
            title = f"{'Bike' if r.service == 'bike' else 'Car'} trip completed" + (f" · rated {'rider' if r.service == 'bike' else 'driver'} {rated.stars}★" if rated else "")
            activity.append(("c-green", title, f"{ref} · {r.pickup_address[:18]} → {r.dropoff_address[:18]} · {r.get_payment_method_display()} · ₦{r.total_amount / 100:,.0f}", r.completed_at or r.requested_at))
        elif r.status in ("cancelled", "no_provider"):
            activity.append(("c-red", f"Trip {ref} {r.get_status_display().lower()}", f"{r.pickup_address[:18]} → {r.dropoff_address[:18]}", r.cancelled_at or r.requested_at))
        else:
            activity.append(("c-orange", f"Trip {ref} {'started' if r.status == 'in_progress' else 'requested'}", f"{r.pickup_address[:18]} → {r.dropoff_address[:18]}{who}", r.started_at or r.requested_at))
    for red in person.promo_redemptions.select_related("promo", "ride")[:2]:
        activity.append(("c-blue", f"Promo {red.promo.code} applied", f"−₦{red.discount_amount / 100:,.0f} on TR-{str(red.ride_id).replace('-', '')[:6].upper()}", red.created_at))
    for t in person.tickets.order_by("-updated_at")[:2]:
        activity.append(("c-grey", f"Support ticket {t.get_status_display().lower()}", f"{t.subject[:40]} · {t.get_category_display()}", t.updated_at))
    activity.append(("c-grey", "Account created", "Phone OTP verified" if person.phone_verified_at else "Phone not verified yet", person.date_joined))
    activity.sort(key=lambda a: a[3], reverse=True)
    audit = AuditLog.objects.filter(target_type="User", target_id=str(person.pk)).select_related("actor").order_by("-created_at").first()
    return {"person": person, "car_trips": by_service.get("car", 0), "bike_trips": by_service.get("bike", 0),
            "spend": (spend["s"] or 0) + (spend["t"] or 0), "methods": methods, "last": last,
            "uses_car": rides.filter(service="car").exists(), "uses_bike": rides.filter(service="bike").exists(),
            "activity": [{"cls": c, "title": t, "sub": sub, "at": at} for c, t, sub, at in activity[:5]], "audit": audit}


@staff_area("users.view")
def customer_list(request):
    return _list(request, customers_only=True)


@staff_area("users.view")
def user_list(request):
    return _list(request, customers_only=False)


@staff_area("users.view")
def user_detail(request, pk):
    person = get_object_or_404(User.objects.select_related("customer_profile", "provider_profile", "wallet"), pk=pk)
    tab = request.GET.get("tab", "rides")
    ctx = {"person": person, "tab": tab, "panel": "customer" if hasattr(person, "customer_profile") else "admin",
           "rides": person.rides.select_related("ride_type", "provider__user")[:20],
           "rides_count": person.rides.count(),
           "tickets": person.tickets.order_by("-updated_at")[:20],
           "payments": person.payments.select_related("ride").order_by("-created_at")[:30],
           "wallet_txs": person.wallet.transactions.order_by("-created_at")[:30] if hasattr(person, "wallet") else [],
           "rating_given": Rating.objects.filter(ride__customer=person, direction="customer_to_provider").aggregate(a=Avg("stars"))["a"]}
    return render(request, "dashboard/users/detail.html", ctx)


@require_POST
@staff_area("users.change")
def user_set_status(request, pk, action):
    """action = suspend | ban | reinstate (POST from the detail page)."""
    if action not in ("suspend", "ban", "reinstate"):
        raise Http404("Unknown action")
    person = get_object_or_404(User, pk=pk)
    new_status = {"suspend": UserStatus.SUSPENDED, "ban": UserStatus.BANNED, "reinstate": UserStatus.ACTIVE}[action]
    reason = request.POST.get("reason", "").strip()
    done = {"suspend": "Account suspended.", "ban": "Account banned.", "reinstate": "Account reinstated."}[action]
    run_action(request, lambda: services.set_user_status(request, person, new_status, reason), done)
    return back(request, "dashboard:user_detail", pk=pk)


@require_POST
@staff_area("users.view")
def user_bulk(request):
    """
    Bulk actions on the Users table (A02 bulk bar). POST ids=<uuid>… and action:
      message  inbox notification + push to the selected people (title, body)     needs users.view + broadcasts
      export   CSV of the selected rows                                            needs users.view
      suspend  suspend the selected accounts (reason)                              needs users.change
    """
    ids = request.POST.getlist("ids")
    action = request.POST.get("action", "")
    people = User.objects.filter(pk__in=ids)
    if not people.exists():
        messages.error(request, "Select at least one person first.")
        return back(request, "dashboard:customers")
    if action == "export":
        return _export(people.select_related("customer_profile"), "chicano-users-selected.csv")
    if action == "message":
        if not has_area(request.user, "broadcasts"):
            messages.error(request, "Your role can't send messages to users.")
            return back(request, "dashboard:customers")
        title, body = request.POST.get("title", "").strip()[:120], request.POST.get("body", "").strip()[:255]
        if not title or not body:
            messages.error(request, "Write a title and a message.")
            return back(request, "dashboard:customers")
        from apps.support.services import notify
        for person in people:
            notify(person, title, body, data={"type": "staff_message"})
            log_action(request, "user.message", person, {"title": title})
        messages.success(request, f"Message sent to {people.count()} {'person' if people.count() == 1 else 'people'}.")
        return back(request, "dashboard:customers")
    if action == "suspend":
        if not has_area(request.user, "users.change"):
            messages.error(request, "Your role can't suspend accounts.")
            return back(request, "dashboard:customers")
        reason = request.POST.get("reason", "").strip()
        if not reason:
            messages.error(request, "Give a reason for suspending.")
            return back(request, "dashboard:customers")
        done, failed = 0, []
        for person in people:
            try:
                services.set_user_status(request, person, UserStatus.SUSPENDED, reason)
                done += 1
            except Exception as exc:       # e.g. your own account, or a staff account
                failed.append(f"{person.full_name or person.phone}: {getattr(exc, 'detail', exc)}")
        if done:
            messages.success(request, f"Suspended {done} account{'s' if done != 1 else ''}.")
        for f in failed:
            messages.error(request, f)
        return back(request, "dashboard:customers")
    raise Http404("Unknown action")
