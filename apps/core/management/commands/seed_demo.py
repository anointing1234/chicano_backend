"""
python manage.py seed_demo

Fills an empty database with everything needed to click through all four apps and the
Super Admin locally. Safe to run more than once (it updates instead of duplicating).

Creates:
  * Ride types: car_standard, car_xl, car_premium, bike  + Lagos fare rules (kobo)
  * Staff (email / password, sign in at http://localhost:8000/dashboard/):
        admin@chicanocruise.test       super_admin
        ops@chicanocruise.test         operations
        compliance@chicanocruise.test  compliance
        finance@chicanocruise.test     finance
        support@chicanocruise.test     support
    password for all: the --password option (default "ChicanoDemo2026!")
  * Customers  (User app · Cars / User app · Bikes):  +2348030000001, +2348030000002
  * Drivers    (Driver app · Cars):  +2348050000001 approved/online (Lekki),
                                     +2348050000002 approved/online (Victoria Island),
                                     +2348050000003 under review (documents to approve)
  * Riders     (Rider app · Bikes):  +2348070000001 approved/online (Yaba),
                                     +2348070000002 under review
  * Promo codes WELCOME10 (10% off first ride, max ₦1,000) and BIKE300 (₦300 off bike rides)
  * Weekly incentives for drivers and riders

Mobile logins use OTP: POST /api/v1/auth/otp/request/ returns `debug_code` while
OTP_DEBUG_RETURN_CODE=true, so no SMS is needed.

Online demo providers only receive offers while their GPS is fresh (< 2 min). Run
`python manage.py dispatch_worker --keep-demo-fresh` in another terminal to keep them
"online" without a phone.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import StaffRole
from apps.customers.models import EmergencyContact, SavedPlace
from apps.customers.services import ensure_customer_profile
from apps.payments.models import Incentive, PromoCode
from apps.payments.services import ensure_wallet, wallet_post
from apps.pricing.models import FareRule, RideType
from apps.providers.models import (REQUIRED_DOCUMENTS, DocumentStatus, ProviderDocument, ProviderProfile, ProviderStatus,
                                   Vehicle, VehicleStatus)

User = get_user_model()

# Lagos landmarks used for demo positions.
LEKKI = (Decimal("6.447400"), Decimal("3.472300"))
VI = (Decimal("6.428100"), Decimal("3.421900"))
YABA = (Decimal("6.509500"), Decimal("3.371100"))
IKEJA = (Decimal("6.601800"), Decimal("3.351500"))

# code, service, name, description, seats, min_year, sort, fare (all kobo): base, per_km, per_min, minimum, booking, cancel, wait/min, commission %
RIDE_TYPES = [
    ("car_standard", "car", "Cruise", "Affordable everyday rides", 4, 2008, 1, (50000, 15000, 2000, 150000, 10000, 50000, 3000, 15)),
    ("car_xl", "car", "Cruise XL", "Bigger cars for up to 6", 6, 2010, 2, (80000, 22000, 3000, 250000, 10000, 70000, 4000, 15)),
    ("car_premium", "car", "Cruise Premium", "Newer cars, top-rated drivers", 4, 2019, 3, (120000, 30000, 4000, 400000, 15000, 100000, 5000, 18)),
    ("bike", "bike", "Bike", "Beat the traffic. Helmet provided", 1, None, 1, (20000, 8000, 1000, 50000, 5000, 20000, 1500, 12)),
]


class Command(BaseCommand):
    help = "Create demo ride types, fares, staff, customers, drivers, riders, promos and incentives."

    def add_arguments(self, parser):
        parser.add_argument("--password", default="ChicanoDemo2026!", help="Password for all demo staff accounts.")

    @transaction.atomic
    def handle(self, *args, **opts):
        types = self._ride_types()
        self._staff(opts["password"])
        self._customers()
        self._providers(types)
        self._promos_and_incentives()
        self.stdout.write(self.style.SUCCESS(
            "Demo data ready.\n"
            "  Swagger:     http://localhost:8000/api/docs/\n"
            "  Dashboard:   http://localhost:8000/dashboard/  admin@chicanocruise.test / " + opts["password"] + "\n"
            "  Customer:    +2348030000001 (OTP debug_code is returned by /auth/otp/request/)\n"
            "  Driver:      +2348050000001   Rider: +2348070000001\n"
            "  Keep demo drivers/riders online: python manage.py dispatch_worker --keep-demo-fresh"))

    # ------------------------------------------------------------------ pricing
    def _ride_types(self) -> dict:
        out = {}
        for code, service, name, desc, seats, min_year, sort, fare in RIDE_TYPES:
            rt, _ = RideType.objects.update_or_create(code=code, defaults=dict(
                service=service, name=name, description=desc, seats=seats, min_vehicle_year=min_year, sort_order=sort, is_active=True))
            base, per_km, per_min, minimum, booking, cancel, wait, commission = fare
            FareRule.objects.update_or_create(ride_type=rt, city="Lagos", is_active=True, defaults=dict(
                base_amount=base, per_km_amount=per_km, per_min_amount=per_min, minimum_amount=minimum,
                booking_fee_amount=booking, cancellation_fee_amount=cancel, wait_per_min_amount=wait,
                commission_percent=commission))
            out[code] = rt
        self.stdout.write(f"  ride types + Lagos fares: {', '.join(out)}")
        return out

    # ------------------------------------------------------------------ people
    def _user(self, phone, first, last, **extra) -> User:
        user, created = User.objects.get_or_create(phone=phone, defaults=dict(first_name=first, last_name=last,
                                                                             phone_verified_at=timezone.now(), **extra))
        return user

    def _staff(self, password):
        roles = [("admin", StaffRole.SUPER_ADMIN, "Amaka", "Nwosu"), ("ops", StaffRole.OPERATIONS, "Bayo", "Adeyemi"),
                 ("compliance", StaffRole.COMPLIANCE, "Ngozi", "Eze"), ("finance", StaffRole.FINANCE, "Femi", "Oladipo"),
                 ("support", StaffRole.SUPPORT, "Zainab", "Bello")]
        for i, (handle, role, first, last) in enumerate(roles, start=1):
            user = self._user(f"+23400000000{i:02d}", first, last, email=f"{handle}@chicanocruise.test")
            user.email, user.is_staff, user.staff_role = f"{handle}@chicanocruise.test", True, role
            user.is_superuser = role == StaffRole.SUPER_ADMIN     # also lets them into /django-admin/
            user.set_password(password)
            user.save()
        self.stdout.write("  staff: admin, ops, compliance, finance, support @chicanocruise.test")

    def _customers(self):
        people = [("+2348030000001", "Ada", "Okafor"), ("+2348030000002", "Tunde", "Bakare")]
        for phone, first, last in people:
            user = self._user(phone, first, last, email=f"{first.lower()}@example.com")
            profile = ensure_customer_profile(user)
            SavedPlace.objects.get_or_create(customer=profile, label="home", defaults=dict(
                name="Home", address="12 Admiralty Way, Lekki Phase 1", lat=LEKKI[0], lng=LEKKI[1], note="Blue gate, opposite GTBank"))
            SavedPlace.objects.get_or_create(customer=profile, label="work", defaults=dict(
                name="Work", address="Adeola Odeku St, Victoria Island", lat=VI[0], lng=VI[1]))
            EmergencyContact.objects.get_or_create(customer=profile, phone="+2348099999999",
                                                   defaults=dict(name="Mum", relationship="Mother"))
            if ensure_wallet(user).balance_amount == 0:
                wallet_post(user, "credit", 1_000_000, note="Demo credit")        # ₦10,000 to try wallet payments
        self.stdout.write("  customers: +2348030000001 (Ada), +2348030000002 (Tunde) with ₦10,000 wallet credit")

    def _providers(self, types):
        now = timezone.now()
        # phone, first, last, service, status, position, vehicle (make, model, year, colour, plate), ride types
        rows = [
            ("+2348050000001", "Emeka", "Obi", "car", ProviderStatus.APPROVED, LEKKI, ("Toyota", "Corolla", 2016, "Silver", "KJA482FT"), ["car_standard"]),
            ("+2348050000002", "Chinedu", "Umeh", "car", ProviderStatus.APPROVED, VI, ("Toyota", "Sienna", 2020, "Black", "LND211AA"), ["car_standard", "car_xl", "car_premium"]),
            ("+2348050000003", "Kelechi", "Ibe", "car", ProviderStatus.UNDER_REVIEW, IKEJA, ("Honda", "Accord", 2014, "Blue", "APP553KD"), []),
            ("+2348070000001", "Musa", "Ibrahim", "bike", ProviderStatus.APPROVED, YABA, ("Bajaj", "Boxer 150", 2022, "Red", "YAB12QK"), ["bike"]),
            ("+2348070000002", "Sani", "Abubakar", "bike", ProviderStatus.UNDER_REVIEW, YABA, ("TVS", "HLX 125", 2023, "Blue", "KTU77RB"), []),
        ]
        for phone, first, last, service, status, pos, veh, codes in rows:
            user = self._user(phone, first, last)
            ensure_wallet(user)
            approved = status == ProviderStatus.APPROVED
            profile, _ = ProviderProfile.objects.update_or_create(user=user, defaults=dict(
                service=service, status=status, city="Lagos",
                is_online=approved, last_lat=pos[0], last_lng=pos[1], last_heading=90, last_location_at=now,
                bank_name="GTBank" if approved else "", bank_account_number="0123456789" if approved else "",
                bank_account_name=f"{first} {last}" if approved else "",
                approved_at=now if approved else None))
            make, model, year, colour, plate = veh
            vehicle, _ = Vehicle.objects.update_or_create(plate_number=plate, defaults=dict(
                provider=profile, kind=service, make=make, model=model, year=year, color=colour,
                seats=1 if service == "bike" else (6 if model == "Sienna" else 4),
                has_rider_helmet=service == "bike", has_passenger_helmet=service == "bike", has_reflective_vest=service == "bike",
                status=VehicleStatus.APPROVED if approved else VehicleStatus.PENDING, is_active=True))
            vehicle.ride_types.set([types[c] for c in codes])
            # Placeholder document files (paths only) so the review queue has something to show.
            for doc_type in REQUIRED_DOCUMENTS[service]:
                ProviderDocument.objects.update_or_create(provider=profile, doc_type=doc_type, defaults=dict(
                    file=f"demo/{doc_type}.jpg", number=f"DEMO-{plate}-{doc_type[:3].upper()}",
                    expires_at=(now + timedelta(days=365)).date(),
                    status=DocumentStatus.APPROVED if approved else DocumentStatus.PENDING))
        self.stdout.write("  drivers: Emeka + Chinedu (online), Kelechi (review) · riders: Musa (online), Sani (review)")

    # ------------------------------------------------------------------ growth
    def _promos_and_incentives(self):
        now = timezone.now()
        PromoCode.objects.update_or_create(code="WELCOME10", defaults=dict(
            description="10% off your first ride (up to ₦1,000)", discount_type="percent", value=10, max_discount_amount=100000,
            service=None, per_user_limit=1, first_ride_only=True, valid_from=now - timedelta(days=1),
            valid_to=now + timedelta(days=365), is_active=True))
        PromoCode.objects.update_or_create(code="BIKE300", defaults=dict(
            description="₦300 off your next 3 bike rides", discount_type="flat", value=30000, service="bike",
            per_user_limit=3, valid_from=now - timedelta(days=1), valid_to=now + timedelta(days=90), is_active=True))
        week_start = (timezone.localtime() - timedelta(days=timezone.localtime().weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        Incentive.objects.update_or_create(service="car", name="Weekly 30", defaults=dict(
            description="Complete 30 car trips this week, earn ₦10,000", target_trips=30, reward_amount=1_000_000,
            starts_at=week_start, ends_at=week_start + timedelta(days=7), min_acceptance_rate=80, is_active=True))
        Incentive.objects.update_or_create(service="bike", name="Weekly 40", defaults=dict(
            description="Complete 40 bike trips this week, earn ₦5,000", target_trips=40, reward_amount=500_000,
            starts_at=week_start, ends_at=week_start + timedelta(days=7), min_acceptance_rate=80, is_active=True))
        self.stdout.write("  promos: WELCOME10, BIKE300 · incentives: Weekly 30 (cars), Weekly 40 (bikes)")
