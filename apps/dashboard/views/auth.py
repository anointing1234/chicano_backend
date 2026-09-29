"""Staff sign-in / sign-out for the dashboard (Django session + CSRF, email + password)."""
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.core.audit import log_action
from apps.core.roles import is_staff_member

from ..forms import LoginForm

User = get_user_model()


def login_view(request):
    """GET: the sign-in page. POST: check email + password of an active staff account."""
    if is_staff_member(request.user):
        return redirect("dashboard:overview")
    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = User.objects.filter(email__iexact=form.cleaned_data["email"], is_staff=True).first()
        if not user or not user.staff_role or not user.check_password(form.cleaned_data["password"]):
            form.add_error(None, "Email or password is incorrect.")
        elif user.status != "active" or not user.is_active:
            form.add_error(None, "This staff account is disabled. Ask a super admin.")
        else:
            # Users log in by phone elsewhere, so name the backend explicitly for the session.
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            log_action(request, "staff.login", user)
            next_url = request.GET.get("next", "")
            if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                next_url = ""
            return redirect(next_url or "dashboard:overview")
    return render(request, "dashboard/login.html", {"form": form})


@require_POST
def logout_view(request):
    logout(request)
    messages.info(request, "You're signed out.")
    return redirect("dashboard:login")
