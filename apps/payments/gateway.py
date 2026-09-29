"""
Card payment gateway adapter.

`DummyGateway` (default) approves every charge, except cards whose token starts with
"fail_" (so the Expo dev can test the "payment failed" screen).

Production: implement `PaystackGateway` (recommended for Nigeria; Flutterwave also works).
Mobile flow with Paystack:
    1. App opens the Paystack checkout (react-native-paystack-webview) for a ₦50 verification charge.
    2. Paystack returns an `authorization_code`; the app sends it to POST /wallet/payment-methods/.
    3. The backend charges saved cards with `charge_authorization` (server-to-server, secret key).
Keys go in env vars (PAYSTACK_SECRET_KEY); never ship a secret key inside the app.
"""
import uuid
from dataclasses import dataclass

from django.conf import settings


@dataclass
class ChargeResult:
    ok: bool
    reference: str
    failure_reason: str = ""


class DummyGateway:
    name = "dummy"

    def charge(self, token: str, amount: int, email: str | None = None) -> ChargeResult:
        ref = f"dummy_{uuid.uuid4().hex[:16]}"
        if token.startswith("fail_"):
            return ChargeResult(False, ref, "Card declined by bank (test card).")
        return ChargeResult(True, ref)

    def refund(self, reference: str, amount: int) -> ChargeResult:
        return ChargeResult(True, f"refund_{uuid.uuid4().hex[:16]}")


def get_gateway():
    if settings.PAYMENT_GATEWAY == "dummy":
        return DummyGateway()
    raise NotImplementedError("Add PaystackGateway in apps/payments/gateway.py and set PAYMENT_GATEWAY=paystack")
