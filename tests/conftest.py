"""
Shared test fixtures.

The `demo` fixture loads the same data as `python manage.py seed_demo`, so tests use the
exact accounts the mobile/admin developers use by hand:
    customers +2348030000001 / +2348030000002
    drivers   +2348050000001 (Lekki, online), +2348050000002 (VI, online), +2348050000003 (under review)
    riders    +2348070000001 (Yaba, online),  +2348070000002 (under review)
    staff     admin@ / ops@ / compliance@ / finance@ / support@ chicanocruise.test
"""
import io

import pytest
from django.core.cache import cache
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.test import APIClient

User = get_user_model()


@pytest.fixture(autouse=True)
def _test_settings(settings):
    settings.OTP_DEBUG_RETURN_CODE = True      # /auth/otp/request/ returns debug_code
    # Throttling off so tests can log in many times; caches reset per test.
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "DEFAULT_THROTTLE_CLASSES": []}
    cache.clear()                               # views with their own throttle (OTP) count in the cache


@pytest.fixture
def demo(db):
    call_command("seed_demo", stdout=io.StringIO())         # quiet (works on Windows too)


@pytest.fixture
def client_for():
    """client_for(user) -> APIClient authenticated as that user (skips OTP; the OTP flow has its own test)."""
    def make(user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c
    return make


@pytest.fixture
def user_by_phone():
    return lambda phone: User.objects.get(phone=phone)


@pytest.fixture
def staff_by_email():
    return lambda handle: User.objects.get(email=f"{handle}@chicanocruise.test")
