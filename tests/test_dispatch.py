"""
Bikes are the dispatch service: they carry packages, not passengers.

    test_bike_booking_needs_package          no package -> 400 package_required; bad recipient phone -> invalid_phone
    test_package_shown_to_customer_and_rider package travels through estimate -> book -> offer -> trip
    test_old_app_note_still_works            an older customer app writes the package into pickup_note
    test_full_delivery_and_wording           collect -> start -> deliver, and the push copy talks about packages
    test_old_rider_app_helmet_check_alias    helmet-check (old name) still records the collection
    test_rider_online_without_passenger_helmet  only the rider's own helmet is needed to go online
    test_car_rides_unchanged                 cars need no package and return package = null
    test_sos_saved_when_sms_fails            an SMS outage never blocks an SOS
    test_parse_delivery_note                 the note format the apps write
"""
import pytest
from rest_framework.test import APIClient

from apps.rides.delivery import landmark_note, parse_delivery_note
from apps.rides.models import Ride

from .test_ride_flows import LEKKI, PACKAGE, SURULERE, VI, YABA

pytestmark = pytest.mark.django_db

RIDER = "+2348070000001"     # seeded approved bike rider (Yaba)
SENDER = "+2348030000002"    # seeded customer


def _quote(c: APIClient, service="bike", pickup=YABA, dropoff=SURULERE, code="bike") -> str:
    r = c.post("/api/v1/rides/estimate/", {"service": service, "pickup": pickup, "dropoff": dropoff}, format="json")
    assert r.status_code == 200, r.content
    return next(q for q in r.json() if q["ride_type"]["code"] == code)["quote_id"]


def _book(c: APIClient, **extra):
    body = {"quote_id": _quote(c), "payment_method": "cash", "pickup_address": "Yaba", "dropoff_address": "Surulere", **extra}
    return c.post("/api/v1/rides/", body, format="json")


def test_bike_booking_needs_package(demo, client_for, user_by_phone):
    sender = client_for(user_by_phone(SENDER))
    r = _book(sender)
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "package_required"
    assert set(err["details"]["package"]) == {"kind", "size", "recipient_name", "recipient_phone"}

    r = _book(sender, package={**PACKAGE, "recipient_phone": "12345"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_phone"

    r = _book(sender, package={**PACKAGE, "size": "Huge"})
    assert r.status_code == 400 and "size" in r.json()["error"]["details"]["package"]
    assert not Ride.objects.filter(service="bike").exists()


def test_package_shown_to_customer_and_rider(demo, client_for, user_by_phone):
    sender = client_for(user_by_phone(SENDER))
    r = _book(sender, package={**PACKAGE, "fragile": True}, pickup_note="Blue gate")
    assert r.status_code == 201, r.content
    ride = r.json()
    assert ride["is_delivery"] is True and ride["requires_helmet"] is False
    assert ride["pickup_note"] == "Blue gate"
    pkg = ride["package"]
    assert pkg["kind"] == "Documents" and pkg["size"] == "Small" and pkg["fragile"] is True
    assert pkg["recipient_name"] == "Ngozi Okafor" and pkg["recipient_phone"] == "+2348034125567"
    assert pkg["collected_at"] is None

    rider = client_for(user_by_phone(RIDER))
    offer = rider.get("/api/v1/provider/offers/current/").json()
    assert offer["package"]["recipient_name"] == "Ngozi Okafor"
    trip = rider.post(f"/api/v1/provider/offers/{offer['id']}/accept/").json()
    assert trip["is_delivery"] is True and trip["helmet_required"] is False
    assert trip["package"]["contents"] == "Signed contract"


def test_old_app_note_still_works(demo, client_for, user_by_phone):
    sender = client_for(user_by_phone(SENDER))
    note = "PACKAGE: Food, Medium, fragile (Jollof for 4) | TO: Tunde Bakare 08123456789 | NOTE: Ask for Musa"
    r = _book(sender, pickup_note=note)
    assert r.status_code == 201, r.content
    ride = r.json()
    assert ride["pickup_note"] == note               # stored as sent, so older rider apps can still read it
    pkg = ride["package"]
    assert (pkg["kind"], pkg["size"], pkg["fragile"], pkg["contents"]) == ("Food", "Medium", True, "Jollof for 4")
    assert pkg["recipient_name"] == "Tunde Bakare" and pkg["recipient_phone"] == "+2348123456789"
    assert landmark_note(Ride.objects.get(pk=ride["id"])) == "Ask for Musa"


def test_full_delivery_and_wording(demo, client_for, user_by_phone):
    sender_user = user_by_phone(SENDER)
    sender = client_for(sender_user)
    rid = _book(sender, package=PACKAGE).json()["id"]
    rider = client_for(user_by_phone(RIDER))
    offer = rider.get("/api/v1/provider/offers/current/").json()
    rider.post(f"/api/v1/provider/offers/{offer['id']}/accept/")
    assert rider.post(f"/api/v1/provider/trips/{rid}/arrive/").status_code == 200

    r = rider.post(f"/api/v1/provider/trips/{rid}/start/")
    assert r.status_code == 409 and r.json()["error"]["code"] == "package_not_collected"
    assert rider.post(f"/api/v1/provider/trips/{rid}/package-collected/").status_code == 200
    assert rider.post(f"/api/v1/provider/trips/{rid}/package-collected/").status_code == 200   # twice is fine
    assert rider.post(f"/api/v1/provider/trips/{rid}/start/").json()["status"] == "in_progress"
    assert rider.post(f"/api/v1/provider/trips/{rid}/complete/").json()["status"] == "completed"

    titles = list(sender_user.notifications.order_by("created_at").values_list("title", flat=True))
    assert "Your rider is coming for the package" in titles
    assert "Your rider is at the pickup" in titles
    assert "Package picked up" in titles
    assert "Package delivered" in titles
    assert not any("driver" in t.lower() or "trip" in t.lower() for t in titles), titles

    rider_titles = list(user_by_phone(RIDER).notifications.values_list("title", flat=True))
    assert "New delivery request" in rider_titles

    events = list(Ride.objects.get(pk=rid).events.values_list("event", flat=True))
    assert events.count("package_collected") == 1


def test_old_rider_app_helmet_check_alias(demo, client_for, user_by_phone):
    sender = client_for(user_by_phone(SENDER))
    rid = _book(sender, package=PACKAGE).json()["id"]
    rider = client_for(user_by_phone(RIDER))
    offer = rider.get("/api/v1/provider/offers/current/").json()
    rider.post(f"/api/v1/provider/offers/{offer['id']}/accept/")
    rider.post(f"/api/v1/provider/trips/{rid}/arrive/")
    t = rider.post(f"/api/v1/provider/trips/{rid}/helmet-check/").json()
    assert t["package_collected_at"] and t["helmet_handed_over_at"]    # old app checks helmet_handed_over_at
    assert rider.post(f"/api/v1/provider/trips/{rid}/start/").status_code == 200


def test_rider_online_without_passenger_helmet(demo, client_for, user_by_phone):
    user = user_by_phone(RIDER)
    rider = client_for(user)
    rider.post("/api/v1/provider/status/", {"is_online": False}, format="json")
    v = user.provider_profile.vehicles.get(is_active=True)
    v.has_passenger_helmet, v.has_rider_helmet = False, True
    v.save()
    r = rider.post("/api/v1/provider/status/", {"is_online": True}, format="json")
    assert r.status_code == 200, r.content

    rider.post("/api/v1/provider/status/", {"is_online": False}, format="json")
    v.has_rider_helmet = False
    v.save()
    r = rider.post("/api/v1/provider/status/", {"is_online": True}, format="json")
    assert r.status_code == 409 and r.json()["error"]["code"] == "helmet_required"


def test_car_rides_unchanged(demo, client_for, user_by_phone):
    c = client_for(user_by_phone("+2348030000001"))
    q = _quote(c, "car", LEKKI, VI, "car_standard")
    r = c.post("/api/v1/rides/", {"quote_id": q, "payment_method": "cash", "pickup_address": "Lekki",
                                  "dropoff_address": "VI", "pickup_note": "PACKAGE: nonsense"}, format="json")
    assert r.status_code == 201, r.content
    assert r.json()["package"] is None and r.json()["is_delivery"] is False
    assert Ride.objects.get(pk=r.json()["id"]).package_kind == ""


def test_sos_saved_when_sms_fails(demo, client_for, user_by_phone, monkeypatch):
    from apps.customers.models import EmergencyContact
    from apps.support.models import SOSAlert
    user = user_by_phone(SENDER)
    EmergencyContact.objects.create(customer=user.customer_profile, name="Mum", phone="+2348039999999")

    def boom(*a, **k):
        raise RuntimeError("SMS provider down")
    monkeypatch.setattr("apps.accounts.services.send_sms", boom)
    r = client_for(user).post("/api/v1/support/sos/", {"lat": "6.5", "lng": "3.37"}, format="json")
    assert r.status_code == 201, r.content
    alert = SOSAlert.objects.get(raised_by=user)
    assert {"name": "Mum", "phone": "+2348039999999", "failed": True} in alert.contacts_notified
    assert all(c["failed"] for c in alert.contacts_notified)


def test_parse_delivery_note():
    p = parse_delivery_note("PACKAGE: Documents, Small, fragile (Signed contract) | TO: Ngozi Okafor 08034125567 | NOTE: Blue gate")
    assert p == {"kind": "Documents", "size": "Small", "contents": "Signed contract", "fragile": True,
                 "recipient_name": "Ngozi Okafor", "recipient_phone": "08034125567"}
    assert parse_delivery_note("Blue gate") is None
    assert parse_delivery_note("") is None
    p = parse_delivery_note("PACKAGE: Parcel, Large | TO: Ada 08012345678")
    assert p["fragile"] is False and p["contents"] == ""


def test_migration_backfills_old_bike_rides(demo, client_for, user_by_phone):
    """Rides booked before the package fields existed get them from their note when the migration runs."""
    import importlib

    from django.apps import apps as django_apps
    from apps.pricing.models import RideType
    sender = client_for(user_by_phone(SENDER))
    rid = _book(sender, pickup_note="PACKAGE: Parcel, Large | TO: Ada Obi 08012345678 | NOTE: Gate 2").json()["id"]
    Ride.objects.filter(pk=rid).update(package_kind="", package_size="", recipient_name="", recipient_phone="")
    RideType.objects.filter(code="bike").update(description="Beat the traffic. Helmet provided")

    mig = importlib.import_module("apps.rides.migrations.0002_dispatch_packages")
    mig.backfill_packages(django_apps, None)
    mig.bike_ride_type_copy(django_apps, None)
    r = Ride.objects.get(pk=rid)
    assert (r.package_kind, r.package_size, r.recipient_name, r.recipient_phone) == ("Parcel", "Large", "Ada Obi", "+2348012345678")
    assert "helmet" not in RideType.objects.get(code="bike").description.lower()
