"""
Ride lifecycle. The ONLY place that changes `Ride.status`.

Every transition:
  1. checks the current status is allowed (otherwise 409 `invalid_state`),
  2. stamps the timestamp field,
  3. writes a RideEvent (timeline for support/disputes),
  4. notifies the other party (push; the app also sees it on its next poll).

Cars carry passengers; bikes are the dispatch service and carry packages, so bike messages talk about deliveries.
"""
from django.db import transaction
from django.db.models import Avg, F
from django.utils import timezone

from apps.core.exceptions import ApiError, Conflict
from apps.pricing.models import FareQuote
from apps.pricing.services import active_rule
from apps.support.services import notify

from . import dispatch
from .models import (ACTIVE_STATUSES, PROVIDER_BUSY_STATUSES, CancelledBy, OfferStatus, Rating, RatingDirection, Ride, RideEvent, RideOffer,
                     RideStatus, RideStop)

# from-status -> allowed to-statuses
TRANSITIONS = {
    RideStatus.SCHEDULED: {RideStatus.SEARCHING, RideStatus.CANCELLED},
    RideStatus.SEARCHING: {RideStatus.ACCEPTED, RideStatus.CANCELLED, RideStatus.NO_PROVIDER},
    RideStatus.ACCEPTED: {RideStatus.ARRIVED, RideStatus.CANCELLED, RideStatus.SEARCHING},   # SEARCHING = provider dropped it
    RideStatus.ARRIVED: {RideStatus.IN_PROGRESS, RideStatus.CANCELLED},
    RideStatus.IN_PROGRESS: {RideStatus.COMPLETED},
}


def _transition(ride: Ride, to: str, actor=None, event: str | None = None, **data) -> Ride:
    if to not in TRANSITIONS.get(ride.status, set()):
        raise Conflict("invalid_state", f"Can't go from '{ride.status}' to '{to}'.", details={"status": ride.status})
    ride.status = to
    RideEvent.objects.create(ride=ride, event=event or to, actor=actor, data={k: str(v) for k, v in data.items()})
    return ride


def _customer_notify(ride, title, body):
    notify(ride.customer, title, body, data={"type": "ride_update", "ride_id": str(ride.id), "status": ride.status})


def _is_delivery(ride) -> bool:
    return ride.service == "bike"


def _job(ride) -> str:
    """'trip' for cars, 'delivery' for bikes."""
    return "delivery" if _is_delivery(ride) else "trip"


def _provider_notify(ride, title, body):
    if ride.provider:
        notify(ride.provider.user, title, body, data={"type": "ride_update", "ride_id": str(ride.id), "status": ride.status})


# =========================================================================== customer actions
@transaction.atomic
def request_ride(customer, *, quote_id, payment_method: str, card=None, pickup_address: str, dropoff_address: str,
                 pickup_note: str = "", stop_addresses: list[str] | None = None, scheduled_for=None,
                 package: dict | None = None) -> Ride:
    """
    Book at the locked quote price. Starts dispatch immediately (or at scheduled time).
    Bike (dispatch) bookings need the package and recipient: `package`, or an older app's note (see rides/delivery.py).
    """
    quote = FareQuote.objects.select_for_update().filter(pk=quote_id, customer=customer).select_related("ride_type").first()
    if not quote:
        raise ApiError("quote_not_found", "Get a new fare estimate first.", status_code=404)
    if quote.used_at:
        raise Conflict("quote_used", "This fare was already used. Get a new estimate.")
    if quote.expires_at < timezone.now():
        raise Conflict("quote_expired", "This fare has expired. Get a new estimate.")
    package_fields = {}
    if quote.ride_type.service == "bike":
        from .delivery import package_from_request
        package_fields = package_from_request(package, pickup_note)
    if Ride.objects.filter(customer=customer, status__in=ACTIVE_STATUSES).exists():
        raise Conflict("active_ride_exists", "You already have a ride in progress.")
    if payment_method == "card" and not card:
        raise ApiError("card_required", "Choose a saved card or pay with cash.", details={"payment_method_id": ["Required for card payments."]})
    if payment_method == "wallet":
        from apps.payments.services import ensure_wallet
        balance = ensure_wallet(customer).balance_amount
        if balance < quote.total_amount:
            raise ApiError("insufficient_wallet", "Your wallet balance is too low for this ride.", status_code=402,
                           details={"balance_amount": balance, "required_amount": quote.total_amount})

    ride = Ride.objects.create(
        customer=customer, service=quote.ride_type.service, ride_type=quote.ride_type, quote=quote,
        status=RideStatus.SCHEDULED if scheduled_for else RideStatus.SEARCHING,
        pickup_lat=quote.pickup_lat, pickup_lng=quote.pickup_lng, pickup_address=pickup_address, pickup_note=pickup_note,
        dropoff_lat=quote.dropoff_lat, dropoff_lng=quote.dropoff_lng, dropoff_address=dropoff_address,
        scheduled_for=scheduled_for, distance_m=quote.distance_m, duration_s=quote.duration_s,
        gross_amount=quote.gross_amount, discount_amount=quote.discount_amount, total_amount=quote.total_amount,
        payment_method=payment_method, card=card, **package_fields,
    )
    for i, stop in enumerate(quote.stops or []):
        address = (stop_addresses or [])[i] if stop_addresses and i < len(stop_addresses) else stop.get("address", f"Stop {i + 1}")
        RideStop.objects.create(ride=ride, order=i + 1, lat=stop["lat"], lng=stop["lng"], address=address)
    quote.used_at = timezone.now()
    quote.save(update_fields=["used_at", "updated_at"])
    RideEvent.objects.create(ride=ride, event="requested", actor=customer,
                             data={"quote_id": str(quote.id), "total_amount": quote.total_amount, "payment_method": payment_method})
    if ride.status == RideStatus.SEARCHING:
        dispatch.offer_next(ride)   # inside the same transaction: deterministic, test-friendly
    return ride


def cancellation_fee_if_cancelled_now(ride: Ride) -> int:
    """What the customer would pay if they cancel right now (shown in the confirm sheet)."""
    if ride.status not in (RideStatus.ACCEPTED, RideStatus.ARRIVED) or not ride.accepted_at:
        return 0
    rule = active_rule(ride.ride_type, ride.city)
    if (timezone.now() - ride.accepted_at).total_seconds() < rule.cancellation_grace_seconds:
        return 0
    return rule.cancellation_fee_amount


@transaction.atomic
def cancel_by_customer(ride: Ride, reason: str = "") -> Ride:
    ride = Ride.objects.select_for_update().get(pk=ride.pk)
    fee = cancellation_fee_if_cancelled_now(ride)
    _transition(ride, RideStatus.CANCELLED, ride.customer, "cancelled_by_customer", reason=reason, fee=fee)
    ride.cancelled_at, ride.cancelled_by, ride.cancel_reason = timezone.now(), CancelledBy.CUSTOMER, reason
    ride.cancellation_fee_amount = fee
    ride.needs_manual_dispatch = False
    ride.save()
    RideOffer.objects.filter(ride=ride, status=OfferStatus.SENT).update(status=OfferStatus.WITHDRAWN)
    if fee:
        from apps.payments.services import charge_cancellation_fee
        charge_cancellation_fee(ride)
    _provider_notify(ride, f"{_job(ride).capitalize()} cancelled", f"The customer cancelled this {_job(ride)}.")
    return ride


# =========================================================================== provider actions
@transaction.atomic
def accept_offer(offer: RideOffer) -> Ride:
    offer = RideOffer.objects.select_for_update().select_related("ride", "provider").get(pk=offer.pk)
    if offer.status != OfferStatus.SENT or offer.expires_at < timezone.now():
        raise Conflict("offer_expired", "This request is no longer available.", details={"offer_status": offer.status})
    ride = Ride.objects.select_for_update().get(pk=offer.ride_id)
    _transition(ride, RideStatus.ACCEPTED, offer.provider.user, provider_id=offer.provider_id)
    offer.status, offer.responded_at = OfferStatus.ACCEPTED, timezone.now()
    offer.save(update_fields=["status", "responded_at", "updated_at"])
    ride.provider = offer.provider
    ride.vehicle = offer.provider.vehicles.filter(is_active=True).first()
    ride.accepted_at = timezone.now()
    ride.needs_manual_dispatch = False
    ride.save()
    type(offer.provider).objects.filter(pk=offer.provider_id).update(offers_accepted=F("offers_accepted") + 1)
    plate = ride.vehicle.plate_number if ride.vehicle else ""
    if _is_delivery(ride):
        _customer_notify(ride, "Your rider is coming for the package", f"{offer.provider.user.first_name} · {plate}")
    else:
        _customer_notify(ride, "Your driver is on the way", f"{offer.provider.user.first_name} · {plate}")
    return ride


@transaction.atomic
def decline_offer(offer: RideOffer) -> None:
    offer = RideOffer.objects.select_for_update().get(pk=offer.pk)
    if offer.status != OfferStatus.SENT:
        raise Conflict("offer_expired", "This request is no longer available.")
    offer.status, offer.responded_at = OfferStatus.DECLINED, timezone.now()
    offer.save(update_fields=["status", "responded_at", "updated_at"])
    RideEvent.objects.create(ride=offer.ride, event="offer_declined", data={"provider_id": str(offer.provider_id)})
    dispatch.offer_next(offer.ride)


@transaction.atomic
def arrive(ride: Ride) -> Ride:
    ride = Ride.objects.select_for_update().get(pk=ride.pk)
    _transition(ride, RideStatus.ARRIVED, ride.provider.user)
    ride.arrived_at = timezone.now()
    ride.save()
    plate = ride.vehicle.plate_number if ride.vehicle else ""
    if _is_delivery(ride):
        _customer_notify(ride, "Your rider is at the pickup", f"Hand over the package. Check the plate: {plate}")
    else:
        _customer_notify(ride, "Your driver is here", f"Check the plate: {plate}")
    return ride


@transaction.atomic
def confirm_package_collected(ride: Ride) -> Ride:
    """Bikes only: the rider has the package and it's secured. Required before the delivery starts."""
    ride = Ride.objects.select_for_update().get(pk=ride.pk)
    if not _is_delivery(ride):
        raise Conflict("not_delivery", "Only bike deliveries have a package to collect.")
    if ride.status != RideStatus.ARRIVED:
        raise Conflict("invalid_state", "Confirm the package at the pickup.", details={"status": ride.status})
    if not ride.package_collected_at:
        ride.package_collected_at = timezone.now()
        # Older rider apps check `helmet_handed_over_at` before calling start; keep it in step.
        ride.helmet_handed_over_at = ride.package_collected_at
        ride.save(update_fields=["package_collected_at", "helmet_handed_over_at", "updated_at"])
        RideEvent.objects.create(ride=ride, event="package_collected", actor=ride.provider.user)
    return ride


@transaction.atomic
def start(ride: Ride) -> Ride:
    ride = Ride.objects.select_for_update().get(pk=ride.pk)
    if _is_delivery(ride) and not ride.package_collected_at:
        raise Conflict("package_not_collected", "Confirm you have the package before starting the delivery.")
    _transition(ride, RideStatus.IN_PROGRESS, ride.provider.user)
    ride.started_at = timezone.now()
    # Waiting charge: minutes beyond the free wait at pickup.
    rule = active_rule(ride.ride_type, ride.city)
    if ride.arrived_at and rule.wait_per_min_amount:
        waited = (ride.started_at - ride.arrived_at).total_seconds() - rule.free_wait_seconds
        if waited > 0:
            ride.wait_charge_amount = int(waited // 60) * rule.wait_per_min_amount
    ride.save()
    if _is_delivery(ride):
        _customer_notify(ride, "Package picked up", f"On the way to {ride.recipient_name or ride.dropoff_address}")
    else:
        _customer_notify(ride, "Trip started", f"Heading to {ride.dropoff_address}")
    return ride


@transaction.atomic
def complete(ride: Ride) -> Ride:
    ride = Ride.objects.select_for_update().get(pk=ride.pk)
    _transition(ride, RideStatus.COMPLETED, ride.provider.user)
    ride.completed_at = timezone.now()
    ride.gross_amount += ride.wait_charge_amount
    ride.total_amount = ride.gross_amount - ride.discount_amount
    ride.save()
    type(ride.provider).objects.filter(pk=ride.provider_id).update(total_trips=F("total_trips") + 1)
    from apps.customers.models import CustomerProfile
    CustomerProfile.objects.filter(user=ride.customer).update(total_trips=F("total_trips") + 1)
    from apps.payments.services import settle_completed_ride
    settle_completed_ride(ride)
    ride.refresh_from_db()
    body = f"Pay ₦{ride.total_amount / 100:,.0f} in cash" if ride.payment_method == "cash" else "Receipt sent to your email"
    if _is_delivery(ride):
        who = f" to {ride.recipient_name}" if ride.recipient_name else ""
        _customer_notify(ride, "Package delivered", f"Handed over{who}. {body}")
    else:
        _customer_notify(ride, "You've arrived", body)
    return ride


@transaction.atomic
def cancel_by_provider(ride: Ride, reason: str = "", no_show: bool = False) -> Ride:
    """
    Provider drops the trip. Before pickup: the ride goes back to searching for someone else.
    No-show (after the free wait at pickup): the ride is cancelled and the customer pays the fee.
    """
    ride = Ride.objects.select_for_update().get(pk=ride.pk)
    provider = ride.provider
    if no_show:
        rule = active_rule(ride.ride_type, ride.city)
        if ride.status != RideStatus.ARRIVED or (timezone.now() - ride.arrived_at).total_seconds() < rule.free_wait_seconds:
            raise Conflict("no_show_too_early", "You can mark a no-show after the free waiting time.",
                           details={"free_wait_seconds": rule.free_wait_seconds})
        _transition(ride, RideStatus.CANCELLED, provider.user, "no_show")
        ride.cancelled_at, ride.cancelled_by = timezone.now(), CancelledBy.PROVIDER
        ride.cancel_reason = "Sender not available" if _is_delivery(ride) else "Customer no-show"
        ride.cancellation_fee_amount = rule.cancellation_fee_amount
        ride.save()
        from apps.payments.services import charge_cancellation_fee
        charge_cancellation_fee(ride)
        if _is_delivery(ride):
            _customer_notify(ride, "Delivery cancelled", "Your rider waited but nobody handed over the package. A cancellation fee applies.")
        else:
            _customer_notify(ride, "Trip cancelled", "Your driver waited but couldn't find you. A cancellation fee applies.")
        return ride

    if ride.status not in (RideStatus.ACCEPTED, RideStatus.ARRIVED):
        raise Conflict("invalid_state", f"You can only cancel before the {_job(ride)} starts.", details={"status": ride.status})
    RideEvent.objects.create(ride=ride, event="cancelled_by_provider", actor=provider.user, data={"reason": reason})
    type(provider).objects.filter(pk=provider.pk).update(trips_cancelled=F("trips_cancelled") + 1)
    # Back to searching: the customer isn't charged and keeps the same fare.
    ride.status = RideStatus.SEARCHING
    ride.provider, ride.vehicle, ride.accepted_at, ride.arrived_at = None, None, None, None
    ride.package_collected_at = ride.helmet_handed_over_at = None
    ride.save()
    who = "rider" if _is_delivery(ride) else "driver"
    _customer_notify(ride, f"Finding you another {who}", f"Your {who} had to cancel. You won't be charged.")
    dispatch.offer_next(ride)   # inside the same transaction: deterministic, test-friendly
    return ride


# =========================================================================== ratings
@transaction.atomic
def rate(ride: Ride, direction: str, stars: int, tags: list[str], comment: str) -> Rating:
    if ride.status != RideStatus.COMPLETED:
        raise Conflict("invalid_state", f"You can rate after the {_job(ride)} is completed.")
    if Rating.objects.filter(ride=ride, direction=direction).exists():
        raise Conflict("already_rated", f"You've already rated this {_job(ride)}.")
    rating = Rating.objects.create(ride=ride, direction=direction, stars=stars, tags=tags, comment=comment)
    if direction == RatingDirection.CUSTOMER_TO_PROVIDER:
        agg = Rating.objects.filter(ride__provider=ride.provider, direction=direction).aggregate(a=Avg("stars"))
        type(ride.provider).objects.filter(pk=ride.provider_id).update(rating_avg=agg["a"], rating_count=F("rating_count") + 1)
    else:
        from apps.customers.models import CustomerProfile
        agg = Rating.objects.filter(ride__customer=ride.customer, direction=direction).aggregate(a=Avg("stars"))
        CustomerProfile.objects.filter(user=ride.customer).update(rating_avg=agg["a"], rating_count=F("rating_count") + 1)
    return rating


# =========================================================================== staff actions
@transaction.atomic
def manual_assign(ride: Ride, provider, staff_user) -> RideOffer:
    """Super Admin > Dispatch: offer the ride to a chosen provider (60 s to accept)."""
    ride = Ride.objects.select_for_update().get(pk=ride.pk)
    if ride.status != RideStatus.SEARCHING:
        raise Conflict("invalid_state", "Only rides that are still searching can be assigned.", details={"status": ride.status})
    if provider.service != ride.service or provider.status != "approved":
        raise ApiError("provider_not_eligible", "This driver/rider can't take this ride.")
    if not provider.is_online:
        raise ApiError("provider_not_eligible", "This driver/rider is offline.")
    if Ride.objects.filter(provider=provider, status__in=PROVIDER_BUSY_STATUSES).exists():
        raise ApiError("provider_not_eligible", "This driver/rider is already on a trip.")
    if not provider.vehicles.filter(is_active=True, status="approved", ride_types=ride.ride_type).exists():
        raise ApiError("provider_not_eligible", "Their approved vehicle isn't set up for this ride type.")
    RideOffer.objects.filter(ride=ride, status=OfferStatus.SENT).update(status=OfferStatus.WITHDRAWN)
    from apps.core.geo import haversine_km
    km = haversine_km(provider.last_lat or ride.pickup_lat, provider.last_lng or ride.pickup_lng, ride.pickup_lat, ride.pickup_lng)
    offer = dispatch.create_offer(ride, provider, km, manual=True)
    RideEvent.objects.create(ride=ride, event="manual_assign", actor=staff_user, data={"provider_id": str(provider.id)})
    return offer


@transaction.atomic
def cancel_by_staff(ride: Ride, staff_user, reason: str) -> Ride:
    ride = Ride.objects.select_for_update().get(pk=ride.pk)
    if ride.status == RideStatus.IN_PROGRESS:
        raise Conflict("invalid_state", f"A {_job(ride)} in progress can't be cancelled; complete it and refund instead.")
    _transition(ride, RideStatus.CANCELLED, staff_user, "cancelled_by_staff", reason=reason)
    ride.cancelled_at, ride.cancelled_by, ride.cancel_reason = timezone.now(), CancelledBy.STAFF, reason
    ride.needs_manual_dispatch = False
    ride.save()
    RideOffer.objects.filter(ride=ride, status=OfferStatus.SENT).update(status=OfferStatus.WITHDRAWN)
    job = _job(ride)
    _customer_notify(ride, f"{job.capitalize()} cancelled", f"Our team cancelled this {job}. You won't be charged.")
    _provider_notify(ride, f"{job.capitalize()} cancelled", f"Our team cancelled this {job}.")
    return ride
