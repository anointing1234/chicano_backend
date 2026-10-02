"""
Drivers & Fleet: drivers (cars) and riders (bikes) — list, profile, application review,
vehicles, documents (with a secure file viewer), earnings and account actions.

Approval flow (compliance): approve each document -> approve the vehicle with its ride types ->
approve the driver/rider. apps/staff/services.py refuses a step that is out of order.
"""
import mimetypes
from datetime import timedelta

from django.contrib import messages
from django.db.models import Count, Exists, OuterRef, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.audit import log_action
from apps.core.roles import has_area
from apps.payments.services import earnings_summary, provider_balance
from apps.providers.models import DocumentStatus, DocumentType, ProviderDocument, ProviderProfile, ProviderStatus, Vehicle
from apps.providers.services import onboarding_checklist
from apps.staff import services

from .. import metrics
from ..access import SESSION_SERVICE_KEY, apply_sort, back, current_service, paginate, run_action, staff_area
from ..forms import ReasonForm, VehicleApproveForm

SORTS = {"name": "user__first_name", "rating": "rating_avg", "trips": "total_trips", "joined": "created_at", "status": "status"}


@staff_area("providers.view")
def provider_list(request):
    g = request.GET
    if g.get("kind") in ("car", "bike"):       # old sidebar links (?kind=) now switch the top-bar service
        request.session[SESSION_SERVICE_KEY] = g["kind"]
    service = current_service(request)
    soon = timezone.localdate() + timedelta(days=30)
    qs = (ProviderProfile.objects.select_related("user")
          .prefetch_related("vehicles")
          .annotate(docs_pending=Count("documents", filter=Q(documents__status=DocumentStatus.PENDING), distinct=True),
                    docs_expiring=Exists(ProviderDocument.objects.filter(provider=OuterRef("pk"), status=DocumentStatus.APPROVED,
                                                                          expires_at__lte=soon))))
    kind = g.get("kind") if g.get("kind") in ("car", "bike") else None   # sidebar: Riders · bikes / Drivers · cars
    if kind or service:
        qs = qs.filter(service=kind or service)
    if g.get("status"):
        qs = qs.filter(status=g["status"])
    if g.get("online") in ("yes", "no"):
        qs = qs.filter(is_online=g["online"] == "yes")
    if g.get("city"):
        qs = qs.filter(city=g["city"])
    if g.get("docs") == "pending":
        qs = qs.filter(docs_pending__gt=0)
    elif g.get("docs") == "expiring":
        qs = qs.filter(docs_expiring=True)
    if g.get("q"):
        s = g["q"].strip()
        qs = qs.filter(Q(user__phone__icontains=s.lstrip("0").replace(" ", "")) | Q(user__first_name__icontains=s) | Q(user__last_name__icontains=s)
                       | Q(user__email__icontains=s) | Q(vehicles__plate_number__icontains=s.replace(" ", ""))).distinct()
    qs, sort = apply_sort(request, qs, SORTS, "-joined")
    return render(request, "dashboard/providers/list.html", {
        "page": paginate(request, qs), "sort": sort, "f": g, "kind": kind, "statuses": ProviderStatus.choices, "cities": metrics.cities()})


@staff_area("providers.view")
def provider_detail(request, pk):
    p = get_object_or_404(ProviderProfile.objects.select_related("user", "approved_by"), pk=pk)
    tab = request.GET.get("tab", "application")
    vehicles = list(p.vehicles.prefetch_related("ride_types").order_by("-is_active", "-created_at"))
    for v in vehicles:
        v.approve_form = VehicleApproveForm(vehicle=v, prefix=f"v{v.pk}")   # checkbox list per vehicle
    today = timezone.localdate()
    documents = list(p.documents.select_related("reviewed_by").order_by("doc_type", "-created_at"))
    for d in documents:
        d.expiring = bool(d.expires_at and today <= d.expires_at <= today + timedelta(days=30))
        d.is_expired = bool(d.expires_at and d.expires_at < today)
    checklist = onboarding_checklist(p)
    balance = provider_balance(p)
    return render(request, "dashboard/providers/detail.html", {
        "p": p, "tab": tab, "vehicles": vehicles, "documents": documents, "checklist": checklist,
        "balance": balance, "balance_negative": balance < 0, "month": earnings_summary(p, "month"),
        "ledger": p.ledger.select_related("ride").order_by("-created_at")[:30] if tab == "earnings" else [],
        "payouts": p.payouts.order_by("-created_at")[:20] if tab == "earnings" else [],
        "trips": p.rides.select_related("ride_type", "customer")[:20] if tab == "trips" else [],
        "panel": "driver"})


@require_POST
@staff_area("providers.approve")
def provider_approve(request, pk):
    p = get_object_or_404(ProviderProfile.objects.select_related("user"), pk=pk)
    run_action(request, lambda: services.approve_provider(request, p), f"{p.user.full_name} is approved and can go online.")
    return redirect("dashboard:provider_detail", pk=pk)


@require_POST
def provider_status(request, pk, action):
    """reject (compliance) · suspend / reinstate (operations, compliance)."""
    if action not in ("reject", "suspend", "reinstate"):
        raise Http404("Unknown action")

    @staff_area("providers.approve" if action == "reject" else "providers.suspend")
    def _do(request):
        p = get_object_or_404(ProviderProfile.objects.select_related("user"), pk=pk)
        reason = request.POST.get("reason", "").strip()
        if action in ("reject", "suspend") and not reason:
            messages.error(request, "Please give a reason. It's shown to the driver/rider.")
        elif action == "reject":
            run_action(request, lambda: services.reject_provider(request, p, reason), "Application rejected. They've been notified.")
        elif action == "suspend":
            run_action(request, lambda: services.suspend_provider(request, p, reason), "Suspended and taken offline.")
        else:
            run_action(request, lambda: services.reinstate_provider(request, p), "Reinstated.")
        return redirect("dashboard:provider_detail", pk=pk)
    return _do(request)


@require_POST
@staff_area("providers.approve")
def vehicle_action(request, pk, action):
    """approve (with ride types) or reject (with reason) a car or bike."""
    if action not in ("approve", "reject"):
        raise Http404("Unknown action")
    vehicle = get_object_or_404(Vehicle.objects.select_related("provider__user"), pk=pk)
    if action == "approve":
        form = VehicleApproveForm(request.POST, vehicle=vehicle, prefix=f"v{vehicle.pk}")
        codes = form.cleaned_data["ride_types"] if form.is_valid() else []
        run_action(request, lambda: services.approve_vehicle(request, vehicle, codes), "Vehicle approved.")
    else:
        reason = request.POST.get("reason", "").strip()
        if not reason:
            messages.error(request, "Please give a reason. It's sent to the driver/rider.")
        else:
            run_action(request, lambda: services.reject_vehicle(request, vehicle, reason), "Vehicle rejected. They've been notified.")
    return back(request, "dashboard:provider_detail", pk=vehicle.provider_id)


# =========================================================================== documents
@staff_area("providers.approve")
def document_list(request):
    """Applications & documents: the review queue (oldest pending first) and application pipeline."""
    service = current_service(request)
    status, doc_type = request.GET.get("status", "pending"), request.GET.get("doc_type", "")
    if status == "pending" and not request.GET.get("list"):
        first = ProviderDocument.objects.filter(status=DocumentStatus.PENDING)
        if request.GET.get("svc") in ("car", "bike"):
            first = first.filter(provider__service=request.GET["svc"])
        elif current_service(request):
            first = first.filter(provider__service=current_service(request))
        if request.GET.get("type"):
            first = first.filter(doc_type=request.GET["type"])
        return _review_board(request, first.select_related("provider__user").order_by("created_at").first())
    soon = timezone.localdate() + timedelta(days=30)
    qs = ProviderDocument.objects.select_related("provider__user", "reviewed_by")
    if status == "expiring":
        qs = qs.filter(status=DocumentStatus.APPROVED, expires_at__lte=soon, expires_at__gte=timezone.localdate())
    elif status != "all":
        qs = qs.filter(status=status)
    if service:
        qs = qs.filter(provider__service=service)
    if doc_type:
        qs = qs.filter(doc_type=doc_type)
    qs = qs.order_by("created_at" if status == "pending" else ("expires_at" if status == "expiring" else "-created_at"))
    providers = ProviderProfile.objects.all()
    if service:
        providers = providers.filter(service=service)
    pipeline = {r["status"]: r["n"] for r in providers.values("status").annotate(n=Count("id"))}
    return render(request, "dashboard/documents/list.html", {
        "page": paginate(request, qs), "status": status, "doc_type": doc_type, "doc_types": DocumentType.choices,
        "pipeline": [(k, str(label), pipeline.get(k, 0)) for k, label in ProviderStatus.choices], "panel": "driver"})


@staff_area("providers.approve")
def document_review(request, pk):
    """One document on the review board (A06)."""
    doc = get_object_or_404(ProviderDocument.objects.select_related("provider__user", "reviewed_by"), pk=pk)
    return _review_board(request, doc)


def _age(dt) -> str:
    mins = int((timezone.now() - dt).total_seconds() // 60)
    return f"{mins} m" if mins < 60 else (f"{mins // 60} h" if mins < 1440 else f"{mins // 1440} d")


def _review_board(request, doc):
    """
    Review board (A06): the pending queue on the left (Service / Type filters), the file in the middle
    (zoom, rotate), checks + expiry date + reject reasons + Reject / Approve on the right.
    """
    service = request.GET.get("svc") if request.GET.get("svc") in ("car", "bike") else (current_service(request) or "")
    doc_type = request.GET.get("type", "")
    queue = ProviderDocument.objects.filter(status=DocumentStatus.PENDING).select_related("provider__user").order_by("created_at")
    if service:
        queue = queue.filter(provider__service=service)
    if doc_type:
        queue = queue.filter(doc_type=doc_type)
    total = queue.count()
    items = list(queue[:40])
    for q in items:
        q.age = _age(q.created_at)
    ctx = {"queue": items, "queue_total": total, "svc": service, "type": doc_type, "doc_types": DocumentType.choices,
           "panel": "driver", "doc": doc}
    if doc:
        p = doc.provider
        ids = [q.pk for q in items]
        nxt = items[ids.index(doc.pk) + 1] if doc.pk in ids and ids.index(doc.pk) + 1 < len(ids) else next((q for q in items if q.pk != doc.pk), None)
        ctype = mimetypes.guess_type(doc.file.name)[0] or "" if doc.file else ""
        size = None
        try:
            missing = not doc.file or not doc.file.storage.exists(doc.file.name)
            if not missing:
                size = doc.file.size
        except Exception:          # storage unreachable: show the message instead of a broken image
            missing = True
        vehicle = p.vehicles.filter(is_active=True).first()
        reupload = p.documents.filter(doc_type=doc.doc_type).exclude(pk=doc.pk).filter(status__in=[DocumentStatus.EXPIRED, DocumentStatus.REJECTED]).first()
        today = timezone.localdate()
        checks = [("ok", "Name on profile", (p.user.full_name or "—").upper()),
                  ("ok" if doc.number else "warn", "Document number", doc.number or "Not entered by the uploader")]
        if vehicle and doc.doc_type in (DocumentType.INSURANCE, DocumentType.VEHICLE_REGISTRATION):
            checks.append(("ok", "Plate on vehicle", vehicle.plate_number))
        if not doc.expires_at:
            checks.append(("warn", "Expiry date missing", "Enter it from the image before approving"))
        elif doc.expires_at < today:
            checks.append(("bad", "Expired", f"Expired {doc.expires_at:%d %b %Y}. Reject and ask for a new one."))
        else:
            checks.append(("ok", "Valid until", doc.expires_at.strftime("%d %b %Y")))
        ctx.update(p=p, next_doc=nxt, is_pdf=ctype == "application/pdf", is_image=ctype.startswith("image/"), file_missing=missing,
                   size=size, vehicle=vehicle, reupload=reupload, checks=checks,
                   reasons=["Blurry photo", "Expired", "Name mismatch", "Wrong document", "Cropped"])
    return render(request, "dashboard/documents/review.html", ctx)


@require_POST
@staff_area("providers.approve")
def document_action(request, pk, action):
    if action not in ("approve", "reject"):
        raise Http404("Unknown action")
    doc = get_object_or_404(ProviderDocument.objects.select_related("provider__user"), pk=pk)
    if action == "approve":
        # The reviewer may type the expiry date read from the image (A06 "Expiry date").
        raw = (request.POST.get("expires_at") or "").strip()
        if raw:
            from datetime import datetime
            parsed = None
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d / %m / %Y", "%d-%m-%Y"):
                try:
                    parsed = datetime.strptime(raw, fmt).date()
                    break
                except ValueError:
                    continue
            if not parsed:
                messages.error(request, "Enter the expiry date as DD/MM/YYYY.")
                return back(request, "dashboard:documents")
            doc.expires_at = parsed
            doc.save(update_fields=["expires_at", "updated_at"])
        run_action(request, lambda: services.approve_document(request, doc), f"{doc.get_doc_type_display()} approved.")
    else:
        form = ReasonForm(request.POST)
        if form.is_valid():
            run_action(request, lambda: services.reject_document(request, doc, form.cleaned_data["reason"]),
                       f"{doc.get_doc_type_display()} rejected. They'll see your reason in the app.")
        else:
            messages.error(request, "Please give a reason so they know what to fix.")
    return back(request, "dashboard:documents")


@staff_area("providers.view")
def document_file(request, pk):
    """
    Streams a driver/rider document to staff who may see driver profiles. Links in the dashboard
    point here instead of /media/, so documents stay private in production (don't serve
    provider_documents/ publicly). Every view is written to the audit log.
    """
    doc = get_object_or_404(ProviderDocument, pk=pk)
    side = "back" if request.GET.get("side") == "back" else "front"
    f = doc.back_file if side == "back" else doc.file
    if not f:
        raise Http404("No file")
    try:
        handle = f.open("rb")
    except (FileNotFoundError, OSError):
        raise Http404("The file is missing from storage.")
    if has_area(request.user, "providers.view"):
        log_action(request, "document.view", doc, {"side": side})
    ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
    response = FileResponse(handle, content_type=ctype)
    response["Content-Disposition"] = f'inline; filename="{doc.doc_type}-{side}{mimetypes.guess_extension(ctype) or ""}"'
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


@require_POST
@staff_area("providers.approve")
def document_remind(request, pk):
    """'Remind' on Expiring documents (A05): push + inbox message to the driver/rider, logged in the audit trail."""
    from apps.core.audit import log_action
    from apps.support.services import notify
    doc = get_object_or_404(ProviderDocument.objects.select_related("provider__user"), pk=pk)
    days = (doc.expires_at - timezone.localdate()).days if doc.expires_at else None
    when = "today" if days == 0 else (f"in {days} day{'s' if days != 1 else ''}" if days and days > 0 else "soon")
    notify(doc.provider.user, "Document expiring", f"Your {doc.get_doc_type_display().split(' (')[0].lower()} expires {when}. Upload a new one to keep "
           f"{'riding' if doc.provider.service == 'bike' else 'driving'}.", data={"type": "provider_document", "doc_type": doc.doc_type})
    log_action(request, "document.remind", doc, {"expires_at": str(doc.expires_at)})
    messages.success(request, f"Reminder sent to {doc.provider.user.full_name}.")
    return back(request, "dashboard:driver_dashboard")
