"""Provider (driver/rider) onboarding and availability logic."""
from django.db import transaction
from django.utils import timezone

from apps.core.exceptions import ApiError, Conflict

from .models import (REQUIRED_DOCUMENTS, DocumentStatus, ProviderProfile, ProviderStatus, Vehicle,
                     VehicleStatus)


def apply(user, service: str, city: str) -> ProviderProfile:
    """Create the provider profile. One profile per user (a person drives cars OR rides bikes)."""
    if hasattr(user, "provider_profile"):
        profile = user.provider_profile
        if profile.service != service:
            raise Conflict("already_provider", f"This account is already registered as a {profile.kind}.",
                           details={"service": profile.service})
        return profile
    from apps.payments.services import ensure_wallet
    ensure_wallet(user)
    return ProviderProfile.objects.create(user=user, service=service, city=city, status=ProviderStatus.APPLIED)


def onboarding_checklist(profile: ProviderProfile) -> dict:
    """
    The 'Your application' screen. Each step: key, title, state (done | action_needed | in_review | todo).
    """
    user = profile.user
    docs = {d.doc_type: d for d in profile.documents.order_by("created_at")}   # latest wins
    required = REQUIRED_DOCUMENTS[profile.service]
    vehicle = profile.vehicles.filter(is_active=True).first()

    def doc_state(doc_type):
        d = docs.get(doc_type)
        if not d:
            return "todo"
        return {"approved": "done", "pending": "in_review", "rejected": "action_needed", "expired": "action_needed"}[d.status]

    doc_states = [doc_state(t) for t in required]
    if all(s == "done" for s in doc_states):
        docs_step = "done"
    elif any(s == "action_needed" for s in doc_states):
        docs_step = "action_needed"
    elif all(s in ("done", "in_review") for s in doc_states):
        docs_step = "in_review"
    else:
        docs_step = "todo"

    steps = [
        {"key": "personal_details", "title": "Personal details", "state": "done" if user.first_name and user.last_name else "todo"},
        {"key": "vehicle", "title": "Your car" if profile.service == "car" else "Your bike",
         "state": "todo" if not vehicle else ("done" if vehicle.status == VehicleStatus.APPROVED else "in_review")},
        {"key": "documents", "title": "Documents", "state": docs_step,
         "documents": [{"doc_type": t, "state": doc_state(t),
                        "rejection_reason": docs[t].rejection_reason if t in docs else ""} for t in required]},
        {"key": "review", "title": "Background check & approval",
         "state": "done" if profile.status == ProviderStatus.APPROVED else ("in_review" if profile.status == ProviderStatus.UNDER_REVIEW else "todo")},
    ]
    done = sum(1 for s in steps if s["state"] == "done")
    return {"status": profile.status, "service": profile.service, "kind": profile.kind,
            "steps_done": done, "steps_total": len(steps), "steps": steps, "can_go_online": profile.status == ProviderStatus.APPROVED}


def maybe_submit_for_review(profile: ProviderProfile) -> None:
    """After an upload, move the application along automatically."""
    if profile.status in (ProviderStatus.APPROVED, ProviderStatus.SUSPENDED):
        return
    uploaded = set(profile.documents.exclude(status=DocumentStatus.REJECTED).values_list("doc_type", flat=True))
    required = set(REQUIRED_DOCUMENTS[profile.service])
    new_status = ProviderStatus.UNDER_REVIEW if required <= uploaded and profile.vehicles.exists() else ProviderStatus.DOCUMENTS_PENDING
    if profile.status != new_status:
        profile.status = new_status
        profile.save(update_fields=["status", "updated_at"])


def set_online(profile: ProviderProfile, online: bool) -> ProviderProfile:
    if online:
        if profile.status != ProviderStatus.APPROVED:
            raise ApiError("provider_not_approved", "Your account isn't approved yet.", status_code=403, details={"status": profile.status})
        vehicle = profile.vehicles.filter(is_active=True, status=VehicleStatus.APPROVED).first()
        if not vehicle:
            raise Conflict("no_approved_vehicle", "Add a vehicle and wait for approval before going online.")
        if profile.service == "bike" and not vehicle.has_passenger_helmet:
            raise Conflict("helmet_required", "You need a passenger helmet to go online.")
        expired = profile.documents.filter(status=DocumentStatus.APPROVED, expires_at__lt=timezone.localdate())
        expired_types = list(expired.values_list("doc_type", flat=True))   # read before the update below
        if expired_types:
            expired.update(status=DocumentStatus.EXPIRED)
            raise Conflict("document_expired", "A document has expired. Upload a new one to keep driving.",
                           details={"doc_types": expired_types})
    else:
        from apps.rides.models import Ride, ACTIVE_STATUSES
        if Ride.objects.filter(provider=profile, status__in=ACTIVE_STATUSES).exists():
            raise Conflict("active_ride", "Finish your current trip before going offline.")
    profile.is_online = online
    profile.save(update_fields=["is_online", "updated_at"])
    return profile


def update_location(profile: ProviderProfile, lat, lng, heading=None) -> None:
    ProviderProfile.objects.filter(pk=profile.pk).update(
        last_lat=lat, last_lng=lng, last_heading=heading, last_location_at=timezone.now())


@transaction.atomic
def add_vehicle(profile: ProviderProfile, data: dict) -> Vehicle:
    """New vehicle becomes the active one; older ones are deactivated."""
    if data.get("kind") and data["kind"] != profile.service:
        raise ApiError("wrong_vehicle_kind", f"A {profile.kind} account needs a {'car' if profile.service == 'car' else 'bike'}.")
    profile.vehicles.update(is_active=False)
    vehicle = Vehicle.objects.create(provider=profile, **{**data, "kind": profile.service, "is_active": True})
    maybe_submit_for_review(profile)
    return vehicle
