"""
Fare calculation.

    fare = max(minimum, base + per_km * km + per_min * minutes) + booking_fee
    discount (promo) is applied on top; the platform funds the discount, not the provider.
"""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from apps.core.exceptions import ApiError
from apps.core.geo import estimate_route

from .models import FareQuote, FareRule, RideType, ServiceZone


def active_rule(ride_type: RideType, city: str | None = None) -> FareRule:
    rule = FareRule.objects.filter(ride_type=ride_type, city=city or settings.DEFAULT_CITY, is_active=True).first()
    if not rule:
        raise ApiError("pricing_unavailable", f"{ride_type.name} isn't available in this city yet.", status_code=409)
    return rule


def check_service_zone(service: str, points: list[tuple]) -> None:
    """Raise `outside_service_zone` if any point is outside every active zone for the service.
    If no zones are configured for a service, the whole city is allowed."""
    zones = list(ServiceZone.objects.filter(service=service, is_active=True))
    if not zones:
        return
    for lat, lng in points:
        if not any(z.contains(Decimal(str(lat)), Decimal(str(lng))) for z in zones):
            raise ApiError("outside_service_zone",
                           "Bikes don't serve this area. Choose a point inside the zone or book a car." if service == "bike"
                           else "We don't operate in this area yet.",
                           status_code=400, details={"lat": lat, "lng": lng})


def compute_fare(rule: FareRule, distance_m: int, duration_s: int) -> dict:
    """Return the breakdown (all kobo)."""
    km = Decimal(distance_m) / 1000
    minutes = Decimal(duration_s) / 60
    distance_part = int(rule.per_km_amount * km)
    time_part = int(rule.per_min_amount * minutes)
    subtotal = rule.base_amount + distance_part + time_part
    fare = max(rule.minimum_amount, subtotal) + rule.booking_fee_amount
    fare = int(round(fare / 5000.0) * 5000)   # round to the nearest ₦50 so cash is easy
    return {"base": rule.base_amount, "distance": distance_part, "time": time_part,
            "booking_fee": rule.booking_fee_amount, "minimum_applied": subtotal < rule.minimum_amount, "gross": fare}


def create_quotes(customer, service: str, pickup: tuple, dropoff: tuple, stops: list[dict], promo_code: str | None) -> list[FareQuote]:
    """One quote per active ride type of the service (e.g. Standard, XL, Premium for cars)."""
    points = [pickup, *[(s["lat"], s["lng"]) for s in stops], dropoff]
    check_service_zone(service, points)
    distance_m, duration_s = estimate_route(points, service)

    promo = None
    if promo_code:
        from apps.payments.services import validate_promo
        promo = validate_promo(customer, promo_code, service)

    quotes = []
    expires = timezone.now() + timedelta(seconds=settings.FARE_QUOTE_TTL_SECONDS)
    for ride_type in RideType.objects.filter(service=service, is_active=True):
        try:
            rule = active_rule(ride_type)
        except ApiError:
            continue   # ride type not priced in this city: just don't offer it
        br = compute_fare(rule, distance_m, duration_s)
        discount = 0
        if promo:
            from apps.payments.services import promo_discount
            discount = promo_discount(promo, br["gross"])
        quotes.append(FareQuote.objects.create(
            customer=customer, ride_type=ride_type,
            pickup_lat=pickup[0], pickup_lng=pickup[1], dropoff_lat=dropoff[0], dropoff_lng=dropoff[1],
            stops=stops, distance_m=distance_m, duration_s=duration_s,
            gross_amount=br["gross"], discount_amount=discount, total_amount=br["gross"] - discount,
            breakdown=br, promo=promo, expires_at=expires,
        ))
    if not quotes:
        raise ApiError("pricing_unavailable", "No ride types are available here right now.", status_code=409)
    return quotes
