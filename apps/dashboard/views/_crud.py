"""
One reusable create/edit page for the simple catalog records (promo codes, incentives, ride
types, fare rules, service zones, staff accounts). Renders dashboard/record_form.html.
"""
from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, render

from apps.core.audit import log_action


def edit_record(request, form_class, instance, *, title: str, back: str, audit_name: str, before_save=None, intro: str = "",
                panel: str = "admin", crumb: tuple[str, str] | None = None, noun: str = "record"):
    """
    GET  -> empty form (instance=None) or the record's current values.
    POST -> validate; save inside a transaction; audit "<audit_name>.create|update"; back to the list.
    `before_save(obj)` runs in the same transaction (e.g. switch off the previous active fare rule).
    """
    form = form_class(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            obj = form.save(commit=False)
            if before_save:
                before_save(obj)
            obj.save()
            form.save_m2m()
        log_action(request, f"{audit_name}.{'update' if instance else 'create'}", obj, {"changed": form.changed_data})
        messages.success(request, f"{noun.capitalize()} {'updated' if instance else 'created'} successfully.")
        return redirect(back)
    if request.method == "POST":
        messages.error(request, "Please correct the highlighted fields.")
    return render(request, "dashboard/record_form.html", {
        "title": title, "form": form, "back": back, "intro": intro, "is_new": instance is None, "panel": panel,
        "crumb": crumb, "noun": noun})
