"""
Support tickets & SOS, and Lost items (Customers & Rides panel).

Tickets: queue (urgent first, then longest waiting), search, filters, owner assignment, public
replies (pushed to the person) and internal notes. SOS: live list (5 s refresh), acknowledge,
resolve. Lost items: every report with its linked ticket; staff update the status and can
notify the customer.
"""
import uuid

from django.contrib import messages
from django.db.models import Case, IntegerField, Q, When
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.core.audit import log_action
from apps.staff import services
from apps.support.models import LostItemReport, LostItemStatus, SOSAlert, SOSStatus, SupportTicket, TicketCategory, TicketMessage
from apps.support.services import notify

from ..access import back, current_service, is_fragment, paginate, run_action, staff_area
from ..forms import LostItemUpdateForm, NotesForm, TicketReplyForm, TicketUpdateForm

PRIORITY_RANK = Case(When(priority="urgent", then=0), When(priority="high", then=1), When(priority="normal", then=2),
                     default=3, output_field=IntegerField())


@staff_area("support")
def support_home(request):
    """?tab=tickets (default) or ?tab=sos."""
    if request.GET.get("tab") == "sos":
        return _sos(request)
    g = request.GET
    status = g.get("status", "open,pending")
    qs = SupportTicket.objects.select_related("user", "assigned_to", "ride").annotate(rank=PRIORITY_RANK)
    if status:
        qs = qs.filter(status__in=status.split(","))
    if g.get("assigned") == "me":
        qs = qs.filter(assigned_to=request.user)
    elif g.get("assigned") == "none":
        qs = qs.filter(assigned_to__isnull=True)
    for key in ("priority", "category"):
        if g.get(key):
            qs = qs.filter(**{key: g[key]})
    if g.get("q"):
        s = g["q"].strip()
        qs = qs.filter(Q(subject__icontains=s) | Q(user__phone__icontains=s.lstrip("0").replace(" ", "")) | Q(user__first_name__icontains=s)
                       | Q(user__last_name__icontains=s) | Q(messages__body__icontains=s)).distinct()
    return render(request, "dashboard/support/tickets.html", {
        "tab": "tickets", "page": paginate(request, qs.order_by("rank", "updated_at")), "status": status, "f": g,
        "categories": TicketCategory.choices, "panel": "customer"})


def _sos(request):
    service = current_service(request)
    status = request.GET.get("status", "active")
    qs = SOSAlert.objects.select_related("raised_by", "ride", "handled_by").order_by("-created_at")
    if status == "active":
        qs = qs.exclude(status=SOSStatus.RESOLVED)
    elif status in SOSStatus.values:
        qs = qs.filter(status=status)
    if service:
        qs = qs.filter(ride__service=service)
    context = {"tab": "sos", "alerts": qs[:100], "status": status, "panel": "customer"}
    if is_fragment(request):
        return render(request, "dashboard/partials/sos_table.html", context)
    return render(request, "dashboard/support/sos.html", context)


@staff_area("support")
def ticket_detail(request, pk):
    ticket = get_object_or_404(SupportTicket.objects.select_related("user", "assigned_to", "ride"), pk=pk)
    lost = LostItemReport.objects.filter(ticket=ticket).first()
    return render(request, "dashboard/support/ticket.html", {
        "ticket": ticket, "thread": ticket.messages.select_related("sender").order_by("created_at"), "lost": lost, "panel": "customer",
        "reply_form": TicketReplyForm(),
        "update_form": TicketUpdateForm(initial={"status": ticket.status, "priority": ticket.priority, "assigned_to": ticket.assigned_to_id}),
        "other_tickets": ticket.user.tickets.exclude(pk=ticket.pk).order_by("-updated_at")[:5]})


@require_POST
@staff_area("support")
def ticket_reply(request, pk):
    ticket = get_object_or_404(SupportTicket.objects.select_related("user"), pk=pk)
    form = TicketReplyForm(request.POST)
    if form.is_valid():
        internal = form.cleaned_data["internal"]
        resolve = request.POST.get("resolve") == "1" and not internal
        services.reply_ticket(request, ticket, form.cleaned_data["body"], internal, "resolved" if resolve else None)
        messages.success(request, "Internal note added." if internal else ("Reply sent and ticket resolved." if resolve else "Reply sent to the customer."))
    else:
        messages.error(request, "Write a message before sending.")
    return redirect("dashboard:ticket_detail", pk=pk)


@require_POST
@staff_area("support")
def ticket_update(request, pk):
    ticket = get_object_or_404(SupportTicket, pk=pk)
    form = TicketUpdateForm(request.POST)
    if form.is_valid():
        before = {"status": ticket.status, "priority": ticket.priority, "assigned_to": str(ticket.assigned_to_id or "")}
        ticket.status, ticket.priority = form.cleaned_data["status"], form.cleaned_data["priority"]
        ticket.assigned_to = form.cleaned_data["assigned_to"]
        ticket.save(update_fields=["status", "priority", "assigned_to", "updated_at"])
        log_action(request, "ticket.update", ticket, {"before": before, "status": ticket.status, "priority": ticket.priority,
                                                       "assigned_to": str(ticket.assigned_to_id or "")})
        messages.success(request, "Ticket updated.")
    else:
        messages.error(request, "Couldn't update the ticket. Check the fields and try again.")
    return redirect("dashboard:ticket_detail", pk=pk)


@require_POST
@staff_area("support")
def ticket_bulk(request):
    """Bulk actions from the ticket queue (the floating bar that appears when rows are ticked)."""
    ids = []
    for raw in request.POST.getlist("ids"):
        try:
            ids.append(uuid.UUID(raw))
        except ValueError:
            continue
    action = request.POST.get("action", "")
    label = services.BULK_TICKET_ACTIONS.get(action, "Updated")
    run_action(request, lambda: services.bulk_update_tickets(request, ids, action),
               f"{label}: {len(ids)} ticket{'s' if len(ids) != 1 else ''}.")
    return back(request, "dashboard:support")


@require_POST
@staff_area("support")
def sos_action(request, pk, action):
    """acknowledge (tells the person help is coming) or resolve (with notes for the record)."""
    if action not in ("acknowledge", "resolve"):
        raise Http404("Unknown action")
    alert = get_object_or_404(SOSAlert.objects.select_related("raised_by"), pk=pk)
    form = NotesForm(request.POST)
    notes = form.cleaned_data["notes"] if form.is_valid() else ""
    to_status = SOSStatus.ACKNOWLEDGED if action == "acknowledge" else SOSStatus.RESOLVED
    run_action(request, lambda: services.update_sos(request, alert, to_status, notes),
               "SOS acknowledged — call them now." if action == "acknowledge" else "SOS resolved.")
    return redirect(reverse("dashboard:support") + "?tab=sos")


# =========================================================================== lost items
@staff_area("support")
def lost_items(request):
    g = request.GET
    status = g.get("status", "open")
    qs = LostItemReport.objects.select_related("customer", "ride__provider__user", "ticket")
    if status == "open":
        qs = qs.exclude(status__in=[LostItemStatus.RETURNED, LostItemStatus.NOT_FOUND])
    elif status in LostItemStatus.values:
        qs = qs.filter(status=status)
    if g.get("q"):
        s = g["q"].strip()
        qs = qs.filter(Q(description__icontains=s) | Q(customer__phone__icontains=s.lstrip("0").replace(" ", "")) | Q(customer__first_name__icontains=s)
                       | Q(customer__last_name__icontains=s) | Q(contact_phone__icontains=s.lstrip("0")))
    return render(request, "dashboard/support/lost_items.html", {
        "page": paginate(request, qs.order_by("-created_at")), "status": status, "statuses": LostItemStatus.choices, "f": g, "panel": "customer"})


@require_POST
@staff_area("support")
def lost_item_update(request, pk):
    report = get_object_or_404(LostItemReport.objects.select_related("customer", "ticket"), pk=pk)
    form = LostItemUpdateForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose a status.")
        return redirect("dashboard:lost_items")
    before = report.status
    report.status = form.cleaned_data["status"]
    report.save(update_fields=["status", "updated_at"])
    note = form.cleaned_data["note"].strip()
    label = LostItemStatus(report.status).label
    if report.ticket and note:
        TicketMessage.objects.create(ticket=report.ticket, sender=request.user, body=note, is_internal_note=not form.cleaned_data["notify_customer"])
    if form.cleaned_data["notify_customer"]:
        notify(report.customer, f"Lost item: {label}", note or f"Update on your {report.get_category_display().lower()}: {label}.",
               data={"type": "lost_item", "ride_id": str(report.ride_id)})
    log_action(request, "lost_item.update", report, {"before": before, "status": report.status, "notified": form.cleaned_data["notify_customer"]})
    messages.success(request, f"Lost item marked “{label}”." + (" The customer was notified." if form.cleaned_data["notify_customer"] else ""))
    return back(request, "dashboard:lost_items")
