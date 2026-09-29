"""
Account business logic: phone normalisation, OTP issue/verify, token issuing, SMS.

Views stay thin; everything testable lives here.
"""
import hashlib
import hmac
import logging
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.exceptions import ApiError

from .models import OTPCode, OtpPurpose, User

log = logging.getLogger(__name__)

_NG_LOCAL = re.compile(r"^0[789][01]\d{8}$")        # 08034125567
_NG_INTL = re.compile(r"^\+?234[789][01]\d{8}$")    # +2348034125567 / 2348034125567


def normalize_phone(raw: str) -> str:
    """
    Accept common Nigerian formats and return E.164 (+234...).
    Raises ApiError(invalid_phone) otherwise. Extend here for new countries.
    """
    phone = re.sub(r"[\s\-()]", "", raw or "")
    if _NG_LOCAL.match(phone):
        return "+234" + phone[1:]
    if _NG_INTL.match(phone):
        return "+" + phone.lstrip("+")
    raise ApiError("invalid_phone", "Enter a valid Nigerian phone number, e.g. 0803 412 5567.",
                   details={"phone": ["Enter a valid Nigerian phone number."]})


def _hash_code(phone: str, code: str) -> str:
    """HMAC the code with the secret key so a DB leak doesn't reveal live codes."""
    return hmac.new(settings.SECRET_KEY.encode(), f"{phone}:{code}".encode(), hashlib.sha256).hexdigest()


def send_sms(phone: str, message: str) -> None:
    """
    Send an SMS. SMS_BACKEND chooses the provider:
      console  development only: the message (with the code) is printed in the server log, nothing is sent
      termii   Termii (Nigeria): TERMII_API_KEY, TERMII_SENDER_ID, optional TERMII_BASE_URL, TERMII_CHANNEL
      twilio   Twilio: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_FROM (number) or TWILIO_MESSAGING_SERVICE_SID
    Raises ApiError(sms_failed) if the provider rejects the message, so the app can say so.
    """
    backend = settings.SMS_BACKEND
    if backend == "console":
        log.info("[SMS to %s] %s", phone, message)
        return

    import requests   # local import: only needed when a real provider is configured

    try:
        if backend == "termii":
            res = requests.post(
                f"{settings.TERMII_BASE_URL.rstrip('/')}/api/sms/send",
                json={
                    "api_key": settings.TERMII_API_KEY,
                    "to": phone.lstrip("+"),          # Termii wants 2348012345678
                    "from": settings.TERMII_SENDER_ID,
                    "sms": message,
                    "type": "plain",
                    "channel": settings.TERMII_CHANNEL,   # "dnd" reaches numbers on Do-Not-Disturb (needs an approved sender ID)
                },
                timeout=15,
            )
        elif backend == "twilio":
            data = {"To": phone, "Body": message}
            if settings.TWILIO_MESSAGING_SERVICE_SID:
                data["MessagingServiceSid"] = settings.TWILIO_MESSAGING_SERVICE_SID
            else:
                data["From"] = settings.TWILIO_FROM
            res = requests.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}/Messages.json",
                data=data,
                auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
                timeout=15,
            )
        else:
            raise ValueError(f"Unknown SMS_BACKEND {backend!r}")
    except requests.RequestException as exc:
        log.error("SMS to %s failed: %s", phone, exc.__class__.__name__)
        raise ApiError("sms_failed", "We couldn't send the code right now. Please try again in a moment.", status_code=502) from exc

    if res.status_code >= 300:
        # Log the provider's reason (never the message itself: it contains the code).
        log.error("SMS to %s rejected by %s: HTTP %s %s", phone, backend, res.status_code, res.text[:300])
        raise ApiError("sms_failed", "We couldn't send the code right now. Please try again in a moment.", status_code=502)


def issue_otp(phone: str, purpose: str = OtpPurpose.LOGIN) -> tuple[OTPCode, str]:
    """Create a fresh code (invalidating older ones) and send it. Returns (otp, plain_code)."""
    code = "".join(secrets.choice("0123456789") for _ in range(settings.OTP_LENGTH))
    with transaction.atomic():
        OTPCode.objects.filter(phone=phone, purpose=purpose, consumed_at__isnull=True).update(consumed_at=timezone.now())
        otp = OTPCode.objects.create(
            phone=phone,
            purpose=purpose,
            code_hash=_hash_code(phone, code),
            expires_at=timezone.now() + timedelta(seconds=settings.OTP_TTL_SECONDS),
        )
    send_sms(phone, f"Your Chicano Cruise code is {code}. It expires in {settings.OTP_TTL_SECONDS // 60} minutes.")
    return otp, code


def verify_otp(phone: str, code: str, purpose: str = OtpPurpose.LOGIN) -> None:
    """Check the latest code for this phone. Raises ApiError on failure; consumes it on success."""
    otp = OTPCode.objects.filter(phone=phone, purpose=purpose, consumed_at__isnull=True).order_by("-created_at").first()
    if not otp or not otp.is_usable:
        raise ApiError("otp_expired", "This code has expired. Request a new one.", status_code=400)
    if otp.attempts >= settings.OTP_MAX_ATTEMPTS:
        raise ApiError("otp_locked", "Too many wrong attempts. Request a new code.", status_code=400)
    if not hmac.compare_digest(otp.code_hash, _hash_code(phone, code)):
        otp.attempts += 1
        otp.save(update_fields=["attempts", "updated_at"])
        remaining = max(settings.OTP_MAX_ATTEMPTS - otp.attempts, 0)
        raise ApiError("otp_invalid", "That code isn't right.", status_code=400,
                       details={"code": ["That code isn't right."], "attempts_remaining": remaining})
    otp.consumed_at = timezone.now()
    otp.save(update_fields=["consumed_at", "updated_at"])


def get_or_create_user_for_phone(phone: str) -> tuple[User, bool]:
    """Login and sign-up are the same step: create the user on first verification."""
    user, created = User.objects.get_or_create(phone=phone)
    if user.phone_verified_at is None:
        user.phone_verified_at = timezone.now()
        user.save(update_fields=["phone_verified_at", "updated_at"])
    return user, created


def tokens_for(user: User) -> dict:
    """Issue a JWT pair. Extra claims let the app route without an extra request."""
    refresh = RefreshToken.for_user(user)
    refresh["roles"] = user.roles
    return {"access": str(refresh.access_token), "refresh": str(refresh)}