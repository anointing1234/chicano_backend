"""
Customers (Customers & Rides panel) and All accounts (Administration).

Both lists read the one combined users table. "Manage customers" shows people with a customer
profile; "All accounts" shows everyone (customers, drivers, riders, staff) with a role filter.
Detail page: profile, wallet, rides, payments, ratings, support tickets; suspend / ban / reinstate.
"""
from django.contrib.auth import get_user_model
from django.db.models import Avg, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import UserStatus
from apps.rides.models import Rating
from apps.staff import services

from ..access import apply_sort, paginate, run_action, staff_area

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
    qs, sort = apply_sort(request, qs, SORTS, "-joined")
    return render(request, "dashboard/users/list.html", {
        "customers_only": customers_only, "page": paginate(request, qs), "sort": sort,
        "role": role, "status": status, "verified": verified, "q": q, "statuses": UserStatus.choices,
        "panel": "customer" if customers_only else "admin"})


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
    return redirect("dashboard:user_detail", pk=pk)
