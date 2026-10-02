"""
Dashboard actions added with the redesign (A01–A08). Each one is posted through the real form/URL.

    test_user_bulk_actions        message / export / suspend the ticked users; roles without access are refused
    test_dispute_no_refund        "No refund" answers and resolves the dispute ticket
    test_document_expiry_and_remind  approve with a typed expiry date, bad dates refused, Remind notifies the rider/driver
    test_payout_bulk              mark ticked payouts paid / failed, export, settled ones are skipped
    test_promo_draft_and_publish  Save draft keeps the code inactive, Publish turns it on
    test_dispatch_assign_bike_delivery  a bike delivery in the Dispatch queue shows the package and can be assigned
"""

import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AuditLog
from apps.payments.models import Payout, PromoCode
from apps.support.models import Notification, SupportTicket
from tests.test_dashboard import _completed_cash_ride, signed_in

pytestmark = pytest.mark.django_db
PACKAGE = {"kind": "Documents", "size": "Small", "contents": "Signed contract", "fragile": False,
           "recipient_name": "Ada Obi", "recipient_phone": "08031234567"}


def test_user_bulk_actions(demo, user_by_phone):
    a, b = user_by_phone("+2348030000001"), user_by_phone("+2348030000002")
    url = reverse("dashboard:user_bulk")
    admin = signed_in("admin")

    r = admin.post(url, {"ids": [a.pk, b.pk], "action": "message", "title": "Hello", "body": "Thanks for using Chicano"}, follow=True)
    assert b"Message sent to 2 people" in r.content
    assert Notification.objects.filter(user__in=[a, b], title="Hello").count() == 2

    r = admin.post(url, {"ids": [a.pk], "action": "message", "title": "", "body": ""}, follow=True)
    assert b"Write a title and a message" in r.content

    r = admin.post(url, {"ids": [a.pk, b.pk], "action": "export"})
    assert r.status_code == 200 and r["Content-Type"].startswith("text/csv")
    assert r.content.decode().count("\n") >= 3                                  # header + 2 rows

    r = admin.post(url, {"ids": [a.pk], "action": "suspend", "reason": ""}, follow=True)
    assert b"Give a reason" in r.content
    a.refresh_from_db()
    assert a.status != "suspended"
    r = admin.post(url, {"ids": [a.pk], "action": "suspend", "reason": "Fraud check"}, follow=True)
    a.refresh_from_db()
    assert b"Suspended 1 account" in r.content and a.status == "suspended"

    r = admin.post(url, {"ids": [], "action": "message"}, follow=True)
    assert b"Select at least one person" in r.content
    assert signed_in("finance").post(url, {"ids": [b.pk], "action": "suspend", "reason": "x"}).status_code == 403


def test_dispute_no_refund(demo, user_by_phone, client_for):
    ride_id = _completed_cash_ride(client_for, user_by_phone)
    ticket = SupportTicket.objects.create(user=user_by_phone("+2348030000001"), ride_id=ride_id, category="fare",
                                          subject="Charged too much")
    support = signed_in("support")
    page = support.get(reverse("dashboard:trip_detail", args=[ride_id])).content.decode()
    assert "Charged too much" in page and reverse("dashboard:trip_no_refund", args=[ride_id]) in page

    url = reverse("dashboard:trip_no_refund", args=[ride_id])
    r = support.post(url, {"reason": "", "resolve_ticket": str(ticket.pk)}, follow=True)
    assert b"why there" in r.content
    r = support.post(url, {"reason": "The fare matches the route taken.", "resolve_ticket": str(ticket.pk)}, follow=True)
    ticket.refresh_from_db()
    assert r.status_code == 200 and ticket.status == "resolved"
    assert ticket.messages.filter(body="The fare matches the route taken.", is_internal_note=False).exists()
    assert signed_in("finance").post(url, {"reason": "x"}).status_code == 403


def test_document_expiry_and_remind(demo, user_by_phone):
    compliance = signed_in("compliance")
    kelechi = user_by_phone("+2348050000003").provider_profile
    doc = kelechi.documents.filter(status="pending").first()
    assert compliance.get(reverse("dashboard:document_review", args=[doc.pk])).status_code == 200
    assert compliance.get(reverse("dashboard:documents")).status_code == 200            # review board

    url = reverse("dashboard:document_action", args=[doc.pk, "approve"])
    r = compliance.post(url, {"expires_at": "31/13/2027"}, follow=True)
    doc.refresh_from_db()
    assert b"DD/MM/YYYY" in r.content and doc.status == "pending"
    compliance.post(url, {"expires_at": "15/03/2027"})
    doc.refresh_from_db()
    assert doc.status == "approved" and str(doc.expires_at) == "2027-03-15"

    doc.expires_at = timezone.localdate() + timedelta(days=5)
    doc.save(update_fields=["expires_at"])
    fleet = compliance.get(reverse("dashboard:driver_dashboard")).content.decode()
    assert reverse("dashboard:document_remind", args=[doc.pk]) in fleet
    r = compliance.post(reverse("dashboard:document_remind", args=[doc.pk]), follow=True)
    assert b"Reminder sent" in r.content
    n = Notification.objects.filter(user=kelechi.user, title="Document expiring").get()
    assert "in 5 days" in n.body
    assert AuditLog.objects.filter(action="document.remind", target_id=str(doc.pk)).exists()
    assert signed_in("finance").post(reverse("dashboard:document_remind", args=[doc.pk])).status_code == 403


def test_payout_bulk(demo, user_by_phone):
    emeka = user_by_phone("+2348050000001").provider_profile
    musa = user_by_phone("+2348070000001").provider_profile
    mk = dict(method="scheduled", bank_name="GTBank", bank_account_number="0123456789", bank_account_name="Test")
    p1 = Payout.objects.create(provider=emeka, amount=500_000, **mk)
    p2 = Payout.objects.create(provider=musa, amount=250_000, **mk)
    done = Payout.objects.create(provider=emeka, amount=100_000, status="paid", **mk)
    finance = signed_in("finance")
    url = reverse("dashboard:payout_bulk")
    assert finance.get(reverse("dashboard:payouts") + "?batch=all").status_code == 200

    r = finance.post(url, {"ids": [p1.pk, p2.pk], "action": "export"})
    assert r.status_code == 200 and "0123456789" in r.content.decode()

    r = finance.post(url, {"ids": [p1.pk, done.pk], "action": "paid", "reference": "TRF-1"}, follow=True)
    p1.refresh_from_db()
    assert p1.status == "paid" and b"1 skipped" in r.content
    finance.post(url, {"ids": [p2.pk], "action": "failed", "reason": "Wrong account"})
    p2.refresh_from_db()
    assert p2.status == "failed" and p2.failure_reason == "Wrong account"
    r = finance.post(url, {"ids": [], "action": "paid"}, follow=True)
    assert b"Tick at least one" in r.content
    assert signed_in("support").post(url, {"ids": [p1.pk], "action": "export"}).status_code == 403


def test_promo_draft_and_publish(demo):
    growth = signed_in("admin")
    url = reverse("dashboard:promo_new")
    base = {"code": "draftme", "discount_type": "percent", "value": "10", "per_user_limit": "1",
            "valid_from": "2026-09-01T00:00", "valid_to": "2027-01-01T00:00"}
    assert growth.post(url, {**base, "publish": "0"}).status_code == 302
    promo = PromoCode.objects.get(code="DRAFTME")
    assert not promo.is_active
    edit = reverse("dashboard:promo_edit", args=[promo.pk])
    assert growth.get(edit).status_code == 200
    growth.post(edit, {**base, "publish": "1"})
    promo.refresh_from_db()
    assert promo.is_active
    page = growth.get(reverse("dashboard:promotions")).content.decode()
    assert "DRAFTME" in page


def test_dispatch_assign_bike_delivery(demo, user_by_phone, client_for):
    from apps.rides.models import Ride
    customer = client_for(user_by_phone("+2348030000001"))
    lekki = {"lat": "6.447400", "lng": "3.472300"}
    q = customer.post("/api/v1/rides/estimate/", {"service": "bike", "pickup": lekki, "dropoff": {"lat": "6.428100", "lng": "3.421900"}},
                      format="json").json()
    ride = customer.post("/api/v1/rides/", {"quote_id": q[0]["quote_id"], "payment_method": "cash", "pickup_address": "Lekki",
                                            "dropoff_address": "VI", "package": PACKAGE}, format="json")
    assert ride.status_code == 201, ride.content
    ride = ride.json()
    ops = signed_in("ops")
    page = ops.get(reverse("dashboard:dispatch") + f"?ride={ride['id']}&wide=1").content.decode()
    assert "Musa Ibrahim" in page                                              # the bike rider is a candidate
    detail = ops.get(reverse("dashboard:trip_detail", args=[ride["id"]])).content.decode()
    assert "Signed contract" in detail

    musa = user_by_phone("+2348070000001").provider_profile
    obj = Ride.objects.get(pk=ride["id"])
    r = ops.post(reverse("dashboard:dispatch_assign", args=[ride["id"]]), {"provider_id": str(musa.pk)}, follow=True)
    assert r.status_code == 200
    assert obj.offers.filter(provider=musa, is_manual=True).exists()


def test_one_service_switch(demo):
    """The top bar holds the only All / Cars / Bikes switch; sidebar Riders/Drivers links and old ?kind= links use it."""
    admin = signed_in("admin")
    for name in ("providers", "trips", "settings", "reports", "incentives", "documents", "overview", "driver_dashboard"):
        page = admin.get(reverse(f"dashboard:{name}") + ("?list=1" if name == "documents" else "")).content.decode()
        assert page.count('class="svc-switch"') == 2, name                 # top bar + phone header copy, nothing in the page body
        assert 'class="segmented" role="group" aria-label="Service"' not in page, name
    page = admin.get(reverse("dashboard:providers") + "?service=bike").content.decode()
    assert "Musa Ibrahim" in page and "Emeka Obi" not in page
    assert re.search(r'href="\?service=bike" aria-pressed="true"', page)       # the switch shows Bikes as chosen
    page = admin.get(reverse("dashboard:providers") + "?kind=car").content.decode()      # old link
    assert "Emeka Obi" in page and "Musa Ibrahim" not in page
    assert admin.session["dashboard_service"] == "car"
