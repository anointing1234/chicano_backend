"""
End-to-end API tests of the main journeys, written against the public HTTP endpoints
exactly as the Expo apps and the admin web app call them.

    test_car_cash_trip_end_to_end   OTP login -> estimate -> book -> offer -> accept -> arrive
                                    -> start -> complete -> collect cash -> ledger maths
    test_bike_delivery_needs_package_collected   bike deliveries can't start before the package is collected
    test_error_envelope             401 / 400 / 403 all use {"error": {code, message, details}}
    test_staff_approves_driver      compliance reviews documents + vehicle, then approves
"""
import pytest
from rest_framework.test import APIClient

from apps.payments.services import provider_balance

pytestmark = pytest.mark.django_db

LEKKI = {"lat": "6.447400", "lng": "3.472300"}
VI = {"lat": "6.428100", "lng": "3.421900"}
YABA = {"lat": "6.509500", "lng": "3.371100"}
SURULERE = {"lat": "6.500000", "lng": "3.354000"}


def _otp_login(phone: str, app: str) -> APIClient:
    """Log in like the mobile app: request a code, verify it, then send the Bearer token."""
    c = APIClient()
    r = c.post("/api/v1/auth/otp/request/", {"phone": phone}, format="json")
    assert r.status_code == 200, r.content
    code = r.json()["debug_code"]
    r = c.post("/api/v1/auth/otp/verify/", {"phone": phone, "code": code, "app": app}, format="json")
    assert r.status_code == 200, r.content
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.json()['access']}")
    return c


PACKAGE = {"kind": "Documents", "size": "Small", "contents": "Signed contract", "fragile": False,
           "recipient_name": "Ngozi Okafor", "recipient_phone": "0803 412 5567"}


def _book(customer: APIClient, service: str, pickup: dict, dropoff: dict, ride_type: str, payment: str = "cash", **extra) -> dict:
    r = customer.post("/api/v1/rides/estimate/", {"service": service, "pickup": pickup, "dropoff": dropoff}, format="json")
    assert r.status_code == 200, r.content
    quote = next(q for q in r.json() if q["ride_type"]["code"] == ride_type)
    r = customer.post("/api/v1/rides/", {"quote_id": quote["quote_id"], "payment_method": payment,
                                         "pickup_address": "Pickup", "dropoff_address": "Dropoff", **extra}, format="json")
    assert r.status_code == 201, r.content
    return r.json()


def test_car_cash_trip_end_to_end(demo, client_for, user_by_phone):
    # 1. Customer logs in with OTP (User app · Cars) and books a Standard car, cash.
    customer = _otp_login("0803 000 0001", "user_cars")
    ride = _book(customer, "car", LEKKI, VI, "car_standard")
    assert ride["status"] == "searching"

    # 2. The nearest driver (Emeka, parked in Lekki) gets the offer on his next poll.
    emeka = user_by_phone("+2348050000001")
    driver = client_for(emeka)
    r = driver.get("/api/v1/provider/offers/current/")
    assert r.status_code == 200, r.content
    offer = r.json()
    assert offer["seconds_left"] > 0

    # 3. Accept -> arrive -> start -> complete.
    r = driver.post(f"/api/v1/provider/offers/{offer['id']}/accept/")
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    rid = ride["id"]
    assert customer.get(f"/api/v1/rides/{rid}/").json()["provider"]["first_name"] == "Emeka"
    for step, expected in (("arrive", "arrived"), ("start", "in_progress"), ("complete", "completed")):
        r = driver.post(f"/api/v1/provider/trips/{rid}/{step}/")
        assert r.status_code == 200, (step, r.content)
        assert r.json()["status"] == expected

    # 4. Cash: the trip stays "current" until the driver confirms the cash.
    trip = driver.get("/api/v1/provider/trips/current/").json()
    assert trip["id"] == rid and trip["cash_due_amount"] > 0
    r = driver.post(f"/api/v1/provider/trips/{rid}/collect-cash/")
    assert r.status_code == 200, r.content
    assert driver.get("/api/v1/provider/trips/current/").status_code == 204

    # 5. Ledger: +fare, -commission, -cash kept  =>  driver owes exactly the commission.
    final = customer.get(f"/api/v1/rides/{rid}/").json()
    assert final["payment_status"] == "paid"
    from apps.rides.models import Ride
    commission = Ride.objects.get(pk=rid).commission_amount
    assert commission > 0
    assert provider_balance(emeka.provider_profile) == -commission

    # 6. Rating (+ tip on a cash ride is added to cash due, so skip tip here).
    r = customer.post(f"/api/v1/rides/{rid}/rate/", {"stars": 5, "tags": ["Smooth driving"], "comment": ""}, format="json")
    assert r.status_code == 201, r.content


def test_bike_delivery_needs_package_collected(demo, client_for, user_by_phone):
    customer = _otp_login("+2348030000002", "user_bikes")
    ride = _book(customer, "bike", YABA, SURULERE, "bike", package=PACKAGE)
    rider = client_for(user_by_phone("+2348070000001"))
    offer = rider.get("/api/v1/provider/offers/current/").json()
    rider.post(f"/api/v1/provider/offers/{offer['id']}/accept/")
    rid = ride["id"]
    assert rider.post(f"/api/v1/provider/trips/{rid}/arrive/").status_code == 200

    # Starting before the package is collected is refused with a specific code.
    r = rider.post(f"/api/v1/provider/trips/{rid}/start/")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "package_not_collected"

    r = rider.post(f"/api/v1/provider/trips/{rid}/package-collected/")
    assert r.status_code == 200 and r.json()["package"]["collected_at"]
    r = rider.post(f"/api/v1/provider/trips/{rid}/start/")
    assert r.status_code == 200 and r.json()["status"] == "in_progress"


def test_error_envelope(demo, client_for, user_by_phone):
    anon = APIClient()
    r = anon.get("/api/v1/rides/")
    assert r.status_code == 401
    body = r.json()["error"]
    assert set(body) == {"code", "message", "details"}

    r = anon.post("/api/v1/auth/otp/verify/", {"phone": "08030000001", "code": "000000", "app": "user_cars"}, format="json")
    assert r.status_code == 400
    assert r.json()["error"]["code"] in ("otp_invalid", "otp_expired")

    r = anon.post("/api/v1/auth/otp/request/", {"phone": "12"}, format="json")
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_phone"
    assert "phone" in r.json()["error"]["details"]      # field-level message for the form

    # A customer can't reach the Super Admin API.
    customer = client_for(user_by_phone("+2348030000001"))
    r = customer.get("/api/v1/staff/overview/")
    assert r.status_code == 403 and r.json()["error"]["code"] == "permission_denied"


def test_staff_approves_driver(demo, client_for, user_by_phone, staff_by_email):
    compliance = client_for(staff_by_email("compliance"))
    kelechi = user_by_phone("+2348050000003").provider_profile
    base = f"/api/v1/staff/providers/{kelechi.id}"

    # Not ready: documents and vehicle still pending.
    r = compliance.post(f"{base}/approve/")
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_ready"

    # Review queue has his documents; approve them all.
    docs = compliance.get(f"/api/v1/staff/documents/?provider={kelechi.id}").json()["results"]
    assert len(docs) == 3
    for d in docs:
        assert compliance.post(f"/api/v1/staff/documents/{d['id']}/approve/").status_code == 200

    # 2014 Honda can't be Premium (2019+), but can be Standard.
    vehicle = kelechi.vehicles.get()
    r = compliance.post(f"/api/v1/staff/vehicles/{vehicle.id}/approve/", {"ride_types": ["car_premium"]}, format="json")
    assert r.status_code == 400 and r.json()["error"]["code"] == "validation_error"
    r = compliance.post(f"/api/v1/staff/vehicles/{vehicle.id}/approve/", {"ride_types": ["car_standard"]}, format="json")
    assert r.status_code == 200

    r = compliance.post(f"{base}/approve/")
    assert r.status_code == 200 and r.json()["status"] == "approved"

    # Finance can't approve drivers; the action is in the audit log for the super admin.
    finance = client_for(staff_by_email("finance"))
    assert finance.post(f"{base}/suspend/", {"reason": "test"}, format="json").status_code == 403
    admin = client_for(staff_by_email("admin"))
    log = admin.get("/api/v1/staff/audit-log/?action=provider.approve").json()["results"]
    assert log and log[0]["target_id"] == str(kelechi.id)


def test_overview_and_schema(demo, client_for, staff_by_email):
    admin = client_for(staff_by_email("admin"))
    for service in ("all", "car", "bike"):
        r = admin.get(f"/api/v1/staff/overview/?service={service}")
        assert r.status_code == 200, r.content
        assert len(r.json()["last_7_days"]) == 7
    assert admin.get("/api/v1/staff/overview/?service=boat").status_code == 400

    # The OpenAPI document the Expo developer generates types from must build cleanly.
    r = APIClient().get("/api/schema/?format=json")
    assert r.status_code == 200
    assert "/api/v1/rides/estimate/" in r.json()["paths"]
