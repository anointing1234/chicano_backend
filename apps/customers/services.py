"""Customer helpers used by other apps."""
from .models import CustomerProfile


def ensure_customer_profile(user) -> CustomerProfile:
    """Create the customer profile (and wallet) the first time someone logs into a user app."""
    profile, _ = CustomerProfile.objects.get_or_create(user=user)
    from apps.payments.services import ensure_wallet   # local import avoids an app import cycle
    ensure_wallet(user)
    return profile
