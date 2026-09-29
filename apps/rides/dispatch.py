"""
Dispatch: matching a ride with the nearest suitable driver (cars) or rider (bikes).

How it works (no WebSockets needed; the apps poll):

    1. POST /rides/ creates the ride (status=searching) and calls `offer_next(ride)`.
    2. `offer_next` picks the nearest eligible provider and creates a RideOffer that expires
       after DISPATCH_OFFER_SECONDS (15 s). The provider sees it via GET /provider/offers/current/
       (and a push notification wakes the app).
    3. Declined or expired offers move on to the next provider. After DISPATCH_MAX_OFFERS
       attempts the ride is flagged `needs_manual_dispatch` for Super Admin > Dispatch
       (it keeps searching; staff can assign someone manually).
    4. `tick()` expires stale offers and starts scheduled rides. It runs:
         - lazily on every relevant poll (so dev works with no background worker), and
         - in production from `python manage.py dispatch_worker` (a loop, every 2 s).

Eligible provider = approved, online, same service, fresh GPS (< 2 min), not on a trip,
active approved vehicle that serves the ride type, within DISPATCH_RADIUS_KM, never offered
this ride before.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.core.geo import eta_seconds, haversine_km
from apps.providers.models import ProviderProfile, ProviderStatus

from .models import PROVIDER_BUSY_STATUSES, OfferStatus, Ride, RideEvent, RideOffer, RideStatus

log = logging.getLogger(__name__)


def find_candidates(ride: Ride, limit: int = 10, radius_km: float | None = None) -> list[tuple[ProviderProfile, float]]:
    """Nearest eligible providers as [(profile, km), ...], closest first. `radius_km` defaults to DISPATCH_RADIUS_KM
    (staff can search wider from Dispatch)."""
    radius = radius_km or settings.DISPATCH_RADIUS_KM
    fresh_after = timezone.now() - timedelta(seconds=settings.PROVIDER_LOCATION_FRESH_SECONDS)
    already_offered = RideOffer.objects.filter(ride=ride).values_list("provider_id", flat=True)
    busy = Ride.objects.filter(status__in=PROVIDER_BUSY_STATUSES, provider__isnull=False).values_list("provider_id", flat=True)
    pending_offers = RideOffer.objects.filter(status=OfferStatus.SENT).values_list("provider_id", flat=True)

    qs = (ProviderProfile.objects
          .filter(service=ride.service, status=ProviderStatus.APPROVED, is_online=True, last_location_at__gte=fresh_after,
                  vehicles__is_active=True, vehicles__status="approved", vehicles__ride_types=ride.ride_type)
          .exclude(user_id=ride.customer_id)
          .exclude(id__in=already_offered).exclude(id__in=busy).exclude(id__in=pending_offers)
          .distinct())

    # Coarse bounding box first (cheap, uses DB), then exact distance in Python.
    deg = radius / 111.0
    qs = qs.filter(last_lat__range=(ride.pickup_lat - _d(deg), ride.pickup_lat + _d(deg)),
                   last_lng__range=(ride.pickup_lng - _d(deg), ride.pickup_lng + _d(deg)))
    scored = []
    for p in qs:
        km = haversine_km(p.last_lat, p.last_lng, ride.pickup_lat, ride.pickup_lng)
        if km <= radius:
            scored.append((p, km))
    scored.sort(key=lambda pk: pk[1])
    return scored[:limit]


def _d(x: float):
    from decimal import Decimal
    return Decimal(str(round(x, 6)))


def create_offer(ride: Ride, provider: ProviderProfile, km: float, manual: bool = False) -> RideOffer:
    offer = RideOffer.objects.create(
        ride=ride, provider=provider, is_manual=manual,
        distance_to_pickup_m=int(km * 1000), eta_to_pickup_s=eta_seconds(km, ride.service),
        expires_at=timezone.now() + timedelta(seconds=settings.DISPATCH_OFFER_SECONDS * (4 if manual else 1)),
    )
    ProviderProfile.objects.filter(pk=provider.pk).update(offers_received=F("offers_received") + 1)
    RideEvent.objects.create(ride=ride, event="offer_sent", data={"provider_id": str(provider.id), "km": round(km, 2), "manual": manual})
    from apps.support.services import notify
    notify(provider.user, "New trip request", f"{ride.pickup_address} · {offer.eta_to_pickup_s // 60 + 1} min away",
           data={"type": "ride_offer", "offer_id": str(offer.id), "ride_id": str(ride.id)})
    return offer


@transaction.atomic
def offer_next(ride: Ride) -> RideOffer | None:
    """Offer the ride to the next best provider, or flag it for manual dispatch."""
    ride = Ride.objects.select_for_update().get(pk=ride.pk)
    if ride.status != RideStatus.SEARCHING:
        return None
    if RideOffer.objects.filter(ride=ride, status=OfferStatus.SENT).exists():
        return None   # someone is still deciding
    attempts = RideOffer.objects.filter(ride=ride).count()
    candidates = find_candidates(ride, limit=1)
    if attempts >= settings.DISPATCH_MAX_OFFERS or not candidates:
        if not ride.needs_manual_dispatch:
            ride.needs_manual_dispatch = True
            ride.save(update_fields=["needs_manual_dispatch", "updated_at"])
            RideEvent.objects.create(ride=ride, event="needs_manual_dispatch", data={"attempts": attempts})
        if not candidates:
            return None
        if attempts >= settings.DISPATCH_MAX_OFFERS:
            return None
    provider, km = candidates[0]
    return create_offer(ride, provider, km)


def expire_stale_offers() -> int:
    """Expire unanswered offers and move their rides on. Returns how many expired."""
    stale = list(RideOffer.objects.filter(status=OfferStatus.SENT, expires_at__lt=timezone.now()).select_related("ride"))
    for offer in stale:
        offer.status = OfferStatus.EXPIRED
        offer.save(update_fields=["status", "updated_at"])
        RideEvent.objects.create(ride=offer.ride, event="offer_expired", data={"provider_id": str(offer.provider_id)})
        offer_next(offer.ride)
    return len(stale)


def start_due_scheduled_rides() -> int:
    """Scheduled rides start searching 15 minutes before pickup time."""
    due = Ride.objects.filter(status=RideStatus.SCHEDULED, scheduled_for__lte=timezone.now() + timedelta(minutes=15))
    count = 0
    for ride in due:
        ride.status = RideStatus.SEARCHING
        ride.save(update_fields=["status", "updated_at"])
        RideEvent.objects.create(ride=ride, event="scheduled_started")
        offer_next(ride)
        count += 1
    return count


def retry_waiting_rides() -> None:
    """Rides still searching with no pending offer (e.g. no one was online) get another try."""
    for ride in Ride.objects.filter(status=RideStatus.SEARCHING).exclude(offers__status=OfferStatus.SENT)[:50]:
        offer_next(ride)


SEARCH_TIMEOUT_MINUTES = 10


def give_up_old_searches() -> int:
    """Rides searching for more than 10 minutes become `no_provider` (the app shows 'No drivers nearby')."""
    cutoff = timezone.now() - timedelta(minutes=SEARCH_TIMEOUT_MINUTES)
    old = Ride.objects.filter(status=RideStatus.SEARCHING, updated_at__lt=cutoff).exclude(offers__status=OfferStatus.SENT)
    count = 0
    for ride in old:
        ride.status = RideStatus.NO_PROVIDER
        ride.needs_manual_dispatch = False
        ride.save(update_fields=["status", "needs_manual_dispatch", "updated_at"])
        RideEvent.objects.create(ride=ride, event="no_provider")
        from apps.support.services import notify
        notify(ride.customer, "No drivers available", "We couldn't find anyone nearby. Please try again.",
               data={"type": "ride_update", "ride_id": str(ride.id), "status": ride.status})
        count += 1
    return count


def tick() -> None:
    """One dispatch cycle. Safe to call often and from several places."""
    expire_stale_offers()
    start_due_scheduled_rides()
    retry_waiting_rides()
    give_up_old_searches()
