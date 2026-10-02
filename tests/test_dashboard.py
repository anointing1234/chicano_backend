"""
Super Admin web dashboard tests (Django test client, real templates, seed_demo data).

    test_login_and_roles            sign-in, wrong password, redirect when signed out, 403 + hidden menu for other roles
    test_every_page_renders         smoke test: every page, tab, filter and detail page returns 200 for a super admin
    test_each_role_sees_only_its_pages
    test_live_endpoints             map JSON, SOS banner, dispatch + SOS fragments
    test_filters_sorting_and_export search/filters/sort change results; CSV export works
    test_approve_driver_by_forms    compliance approves documents -> vehicle -> driver through the HTML forms
    test_document_files_are_private only permitted staff can open a driver's document file
    test_refund_and_cancel          partial refund via the form updates the ride; support refund cap
    test_broadcast_sends_once       one broadcast per token (no duplicate mass notifications)
    test_record_forms               create a promo code (naira → kobo) and keep input on validation errors
    test_manual_dispatch            nobody within 5 km -> "search wider" finds drivers; offline/busy drivers are refused
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.core.models import AuditLog
from apps.payments.models import PromoCode
from apps.providers.models import ProviderStatus
from apps.support.models import Notification

pytestmark = pytest.mark.django_db
PASSWORD = "ChicanoDemo2026!"   # seed_demo's default staff password


def signed_in(handle: str) -> Client:
    c = Client()
    r = c.post(reverse("dashboard:login"), {"email": f"{handle}@chicanocruise.test", "password": PASSWORD})
    assert r.status_code == 302, r.content[:500]
    return c


def _completed_cash_ride(client_for, user_by_phone):
    """Book and finish a real ride through the API (same path the apps use)."""
    customer = client_for(user_by_phone("+2348030000001"))
    q = customer.post("/api/v1/rides/estimate/", {"service": "car", "pickup": {"lat": "6.447400", "lng": "3.472300"},
                                                  "dropoff": {"lat": "6.428100", "lng": "3.421900"}}, format="json").json()
    quote = next(x for x in q if x["ride_type"]["code"] == "car_standard")
    ride = customer.post("/api/v1/rides/", {"quote_id": quote["quote_id"], "payment_method": "cash",
                                            "pickup_address": "A", "dropoff_address": "B"}, format="json").json()
    driver = client_for(user_by_phone("+2348050000001"))
    offer = driver.get("/api/v1/provider/offers/current/").json()
    driver.post(f"/api/v1/provider/offers/{offer['id']}/accept/")
    for step in ("arrive", "start", "complete", "collect-cash"):
        assert driver.post(f"/api/v1/provider/trips/{ride['id']}/{step}/").status_code == 200
    return ride["id"]


def test_login_and_roles(demo, user_by_phone):
    anon = Client()
    r = anon.get(reverse("dashboard:trips"))
    assert r.status_code == 302 and reverse("dashboard:login") in r["Location"]

    r = anon.post(reverse("dashboard:login"), {"email": "admin@chicanocruise.test", "password": "wrong"})
    assert r.status_code == 200 and b"Email or password is incorrect" in r.content

    finance = signed_in("finance")
    assert finance.get(reverse("dashboard:payouts")).status_code == 200
    assert finance.get(reverse("dashboard:documents")).status_code == 403      # compliance only
    menu = finance.get(reverse("dashboard:overview")).content.decode()
    nav = menu.split('<nav', 1)[1].split('</nav>', 1)[0]
    assert f'href="{reverse("dashboard:payouts")}"' in nav and f'href="{reverse("dashboard:documents")}"' not in nav

    # Customers can't use the dashboard even with a password set.
    ada = user_by_phone("+2348030000001")
    ada.set_password(PASSWORD)
    ada.save()
    r = Client().post(reverse("dashboard:login"), {"email": ada.email, "password": PASSWORD})
    assert r.status_code == 200 and b"incorrect" in r.content


def test_every_page_renders(demo, user_by_phone, client_for):
    ride_id = _completed_cash_ride(client_for, user_by_phone)
    admin = signed_in("admin")
    emeka = user_by_phone("+2348050000001")
    kelechi = user_by_phone("+2348050000003").provider_profile
    doc = kelechi.documents.first()
    promo = PromoCode.objects.first()
    base = [
        ("overview", ""), ("overview", "?range=today"), ("overview", "?range=30d&service=bike"), ("overview", "?range=custom&from=2026-01-01&to=2026-01-31"),
        ("customer_dashboard", ""), ("driver_dashboard", "?service=car"), ("reports", "?range=30d"),
        ("dispatch", ""), ("dispatch", "?ride=not-a-uuid"), ("search", "?q=emeka"), ("search", "?q=0803"),
        ("customers", ""), ("customers", "?sort=-trips&verified=yes"), ("users", "?role=rider"),
        ("trips", ""), ("trips", "?status=completed&sort=fare"), ("trips", "?attention=cash"), ("trips", f"?q={ride_id[:8]}"),
        ("payments", ""), ("payments", "?tab=refunds"), ("payments", "?tab=failed"), ("payments", "?tab=disputes"),
        ("promotions", ""), ("promotions", "?state=live"), ("incentives", ""),
        ("support", ""), ("support", "?tab=sos"), ("lost_items", ""),
        ("providers", ""), ("providers", "?docs=pending&sort=rating"), ("documents", ""), ("documents", "?status=expiring"),
        ("payouts", ""), ("payouts", "?tab=commission"),
        ("broadcasts", ""), ("settings", ""), ("settings", "?tab=ride-types"), ("settings", "?tab=zones"),
        ("staff", ""), ("audit", ""),
        ("promo_new", ""), ("incentive_new", ""), ("fare_new", ""), ("ride_type_new", ""), ("zone_new", ""), ("staff_new", ""),
    ]
    urls = [reverse(f"dashboard:{name}") + qs for name, qs in base] + [
        reverse("dashboard:trip_detail", args=[ride_id]),
        reverse("dashboard:provider_detail", args=[emeka.provider_profile.pk]),
        reverse("dashboard:provider_detail", args=[emeka.provider_profile.pk]) + "?tab=earnings",
        reverse("dashboard:provider_detail", args=[emeka.provider_profile.pk]) + "?tab=trips",
        reverse("dashboard:provider_detail", args=[kelechi.pk]) + "?tab=details",
        reverse("dashboard:user_detail", args=[emeka.pk]),
        reverse("dashboard:user_detail", args=[user_by_phone("+2348030000001").pk]) + "?tab=payments",
        reverse("dashboard:user_detail", args=[user_by_phone("+2348030000001").pk]) + "?tab=wallet",
        reverse("dashboard:document_review", args=[doc.pk]),
        reverse("dashboard:promo_edit", args=[promo.pk]),
    ]
    for url in urls:
        r = admin.get(url)
        assert r.status_code == 200, (url, r.status_code)


def test_each_role_sees_only_its_pages(demo):
    expectations = {
        "ops": {"dispatch": 200, "payouts": 403, "documents": 403, "broadcasts": 200, "staff": 403},
        "compliance": {"documents": 200, "dispatch": 403, "payments": 403, "providers": 200},
        "finance": {"payouts": 200, "payments": 200, "reports": 200, "dispatch": 403, "broadcasts": 403},
        "support": {"support": 200, "lost_items": 200, "payments": 200, "payouts": 403, "audit": 403},
    }
    for handle, pages in expectations.items():
        c = signed_in(handle)
        for name, status in pages.items():
            assert c.get(reverse(f"dashboard:{name}")).status_code == status, (handle, name)


def test_live_endpoints(demo):
    admin = signed_in("admin")
    data = admin.get(reverse("dashboard:live_data")).json()
    assert {"drivers", "rides"} <= set(data)
    assert any(d["service"] == "bike" for d in data["drivers"])            # Musa is online in Yaba
    for name in ("sos_banner", "dispatch"):
        r = admin.get(reverse(f"dashboard:{name}"), HTTP_X_FRAGMENT="1")
        assert r.status_code == 200 and b"<html" not in r.content
    r = admin.get(reverse("dashboard:support") + "?tab=sos", HTTP_X_FRAGMENT="1")
    assert r.status_code == 200 and b"<html" not in r.content


def test_filters_sorting_and_export(demo):
    admin = signed_in("admin")
    riders = admin.get(reverse("dashboard:providers") + "?service=bike").content.decode()
    assert "Musa Ibrahim" in riders and "Emeka Obi" not in riders
    # the business-line switch is remembered in the session, so switch back to "all" first
    found = admin.get(reverse("dashboard:providers") + "?service=all&q=KJA482").content.decode()
    assert "Emeka Obi" in found and "Musa Ibrahim" not in found
    empty = admin.get(reverse("dashboard:providers") + "?q=nobody-xyz").content.decode()
    assert "No drivers or riders match" in empty and "Clear filters" in empty
    r = admin.get(reverse("dashboard:customers") + "?sort=name")
    assert r.status_code == 200 and 'aria-sort="ascending"' in r.content.decode()
    csv = admin.get(reverse("dashboard:trips") + "?export=csv")
    assert csv["Content-Type"].startswith("text/csv") and b"ride_id" in csv.content
    report = admin.get(reverse("dashboard:reports") + "?export=csv")
    assert report["Content-Type"].startswith("text/csv") and b"completed_trips" in report.content


def test_approve_driver_by_forms(demo, user_by_phone):
    compliance = signed_in("compliance")
    kelechi = user_by_phone("+2348050000003").provider_profile

    r = compliance.post(reverse("dashboard:provider_approve", args=[kelechi.pk]), follow=True)
    assert b"First approve" in r.content                                  # refused: out of order
    kelechi.refresh_from_db()
    assert kelechi.status == ProviderStatus.UNDER_REVIEW

    for doc in kelechi.documents.all():
        compliance.post(reverse("dashboard:document_action", args=[doc.pk, "approve"]))
    vehicle = kelechi.vehicles.get()
    compliance.post(reverse("dashboard:vehicle_action", args=[vehicle.pk, "approve"]), {f"v{vehicle.pk}-ride_types": ["car_standard"]})
    r = compliance.post(reverse("dashboard:provider_approve", args=[kelechi.pk]), follow=True)
    assert b"is approved" in r.content
    kelechi.refresh_from_db()
    assert kelechi.status == ProviderStatus.APPROVED

    doc = kelechi.documents.first()
    r = compliance.post(reverse("dashboard:document_action", args=[doc.pk, "reject"]), {"reason": ""}, follow=True)
    doc.refresh_from_db()
    assert doc.status == "approved"                                        # reason is mandatory
    compliance.post(reverse("dashboard:document_action", args=[doc.pk, "reject"]), {"reason": "Blurry photo"})
    doc.refresh_from_db()
    assert doc.status == "rejected" and doc.rejection_reason == "Blurry photo"


def test_document_files_are_private(demo, user_by_phone, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    kelechi = user_by_phone("+2348050000003").provider_profile
    doc = kelechi.documents.first()
    doc.file = SimpleUploadedFile("licence.png", b"\x89PNG\r\n\x1a\n" + b"0" * 32, content_type="image/png")
    doc.save()
    url = reverse("dashboard:document_file", args=[doc.pk])
    assert Client().get(url).status_code == 302                            # signed out → login
    assert signed_in("finance").get(url).status_code == 403               # role can't see driver files
    r = signed_in("compliance").get(url)
    assert r.status_code == 200 and r["Content-Type"] == "image/png" and "no-store" in r["Cache-Control"]
    assert AuditLog.objects.filter(action="document.view", target_id=str(doc.pk)).exists()


def test_refund_and_cancel(demo, user_by_phone, client_for):
    ride_id = _completed_cash_ride(client_for, user_by_phone)
    from apps.rides.models import Ride
    ride = Ride.objects.get(pk=ride_id)
    support = signed_in("support")
    big = f"{(ride.total_amount / 100):.2f}"
    if ride.total_amount > 500_000:                                        # support is capped at ₦5,000
        r = support.post(reverse("dashboard:trip_refund", args=[ride_id]), {"amount": big, "reason": "x"}, follow=True)
        assert b"need a finance" in r.content
    finance = signed_in("finance")
    r = finance.post(reverse("dashboard:trip_refund", args=[ride_id]), {"amount": "5.00", "reason": "Detour"}, follow=True)
    assert b"Partial refund" in r.content
    ride.refresh_from_db()
    assert ride.refunded_amount == 500 and ride.payment_status == "partially_refunded"
    r = finance.post(reverse("dashboard:trip_refund", args=[ride_id]), {"amount": "", "reason": ""}, follow=True)
    assert b"Please enter an amount" in r.content


def test_broadcast_sends_once(demo):
    ops = signed_in("ops")
    page = ops.get(reverse("dashboard:broadcasts")).content.decode()
    token = page.split('name="token" value="')[1].split('"')[0]
    data = {"audience": "riders", "title": "Rain", "body": "Ride safe", "confirm": "on", "token": token}
    before = Notification.objects.filter(title="Rain").count()
    ops.post(reverse("dashboard:broadcasts"), data)
    after_first = Notification.objects.filter(title="Rain").count()
    ops.post(reverse("dashboard:broadcasts"), data)                        # same token again (double submit)
    assert after_first > before
    assert Notification.objects.filter(title="Rain").count() == after_first
    assert AuditLog.objects.filter(action="broadcast.send").count() == 1


def test_record_forms(demo):
    growth = signed_in("ops")
    url = reverse("dashboard:promo_new")
    bad = growth.post(url, {"code": "RAIN20", "discount_type": "percent", "value": "150", "per_user_limit": "1",
                            "valid_from": "2026-09-01T00:00", "valid_to": "2026-10-01T00:00", "is_active": "on"})
    assert bad.status_code == 200 and b"can&#x27;t be more than 100" in bad.content and b"RAIN20" in bad.content   # input kept
    ok = growth.post(url, {"code": "rain20", "discount_type": "percent", "value": "20", "max_discount_amount": "1500.00",
                           "per_user_limit": "1", "valid_from": "2026-09-01T00:00", "valid_to": "2026-10-01T00:00", "is_active": "on"})
    assert ok.status_code == 302
    promo = PromoCode.objects.get(code="RAIN20")
    assert promo.max_discount_amount == 150_000                            # ₦1,500.00 stored as kobo


    assert promo.value == 20

    # A fixed-amount promo is typed in naira and stored in kobo; editing shows naira again.
    ok = growth.post(url, {"code": "FLAT300", "discount_type": "flat", "value": "300", "per_user_limit": "1",
                           "valid_from": "2026-09-01T00:00", "valid_to": "2026-10-01T00:00", "is_active": "on"})
    assert ok.status_code == 302
    flat = PromoCode.objects.get(code="FLAT300")
    assert flat.value == 30_000
    edit = growth.get(reverse("dashboard:promo_edit", args=[flat.pk])).content.decode()
    assert 'value="300.00"' in edit


def test_manual_dispatch(demo, client_for, user_by_phone):
    from apps.providers.models import ProviderProfile
    from apps.rides.models import Ride

    customer = client_for(user_by_phone("+2348030000001"))
    ikeja = {"lat": "6.601800", "lng": "3.351500"}                 # no approved driver within 5 km of Ikeja
    q = customer.post("/api/v1/rides/estimate/", {"service": "car", "pickup": ikeja, "dropoff": {"lat": "6.428100", "lng": "3.421900"}},
                      format="json").json()
    quote = next(x for x in q if x["ride_type"]["code"] == "car_standard")
    ride = customer.post("/api/v1/rides/", {"quote_id": quote["quote_id"], "payment_method": "cash",
                                            "pickup_address": "Allen Avenue", "dropoff_address": "VI"}, format="json").json()
    ops = signed_in("ops")
    page = ops.get(reverse("dashboard:dispatch") + f"?ride={ride['id']}").content.decode()
    assert "Nobody free within 5 km" in page and f"?ride={ride['id']}&amp;wide=1" in page
    wide = ops.get(reverse("dashboard:dispatch") + f"?ride={ride['id']}&wide=1").content.decode()
    assert "Emeka Obi" in wide and " km<" in wide

    emeka = user_by_phone("+2348050000001").provider_profile
    ProviderProfile.objects.filter(pk=emeka.pk).update(is_online=False)
    r = ops.post(reverse("dashboard:dispatch_assign", args=[ride["id"]]), {"provider_id": str(emeka.pk)}, follow=True)
    assert b"offline" in r.content and not Ride.objects.get(pk=ride["id"]).offers.filter(provider=emeka, is_manual=True).exists()

    ProviderProfile.objects.filter(pk=emeka.pk).update(is_online=True)
    r = ops.post(reverse("dashboard:dispatch_assign", args=[ride["id"]]), {"provider_id": str(emeka.pk)}, follow=True)
    assert b"Offer sent to Emeka Obi" in r.content
    assert Ride.objects.get(pk=ride["id"]).offers.filter(provider=emeka, is_manual=True, status="sent").exists()


def test_ticket_bulk_actions_and_page_size(demo, client_for, user_by_phone):
    from apps.support.models import SupportTicket

    ada = client_for(user_by_phone("+2348030000001"))
    for n in range(12):                                   # > 10 so the "rows per page" picker shows
        ada.post("/api/v1/support/tickets/", {"category": "fare", "subject": f"Question {n}", "message": "Hello"}, format="json")
    ids = [str(pk) for pk in SupportTicket.objects.values_list("pk", flat=True)[:2]]

    # Finance can't use the support desk, so bulk actions are refused on the server.
    assert signed_in("finance").post(reverse("dashboard:ticket_bulk"), {"ids": ids, "action": "resolve"}).status_code == 403

    support = signed_in("support")
    r = support.post(reverse("dashboard:ticket_bulk"), {"ids": ids, "action": "assign_me"}, follow=True)
    assert b"Assigned to you: 2 tickets" in r.content
    me = user_by_phone("+2348030000001").__class__.objects.get(email="support@chicanocruise.test")
    assert SupportTicket.objects.filter(pk__in=ids, assigned_to=me).count() == 2
    r = support.post(reverse("dashboard:ticket_bulk"), {"ids": ids + ["not-a-uuid"], "action": "resolve"}, follow=True)
    assert SupportTicket.objects.filter(pk__in=ids, status="resolved").count() == 2
    assert AuditLog.objects.filter(action="ticket.update", data__bulk=True).count() == 4
    r = support.post(reverse("dashboard:ticket_bulk"), {"ids": ids, "action": "delete_everything"}, follow=True)
    assert b"Unknown bulk action" in r.content

    admin = signed_in("admin")
    page = admin.get(reverse("dashboard:support") + "?status=&per_page=10").content.decode()
    assert 'name="per_page"' in page and '<option value="10" selected>' in page
    assert admin.get(reverse("dashboard:support") + "?per_page=9999").status_code == 200       # ignored, default size
    assert admin.get(reverse("dashboard:audit") + "?actor=not-a-uuid").status_code == 200       # no crash on a bad link
