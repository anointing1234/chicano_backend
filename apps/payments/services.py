"""
Payment, wallet, promo and provider-earnings logic.

Rules of thumb:
* Every function that moves money runs inside `transaction.atomic()` and locks the row
  it changes (`select_for_update`) so two requests can't double-spend.
* Amounts are kobo integers.
* The platform funds promo discounts: the provider's ledger is credited the full gross fare.
"""
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Count, F, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.core.exceptions import ApiError, Conflict

from .gateway import get_gateway
from .models import (Incentive, IncentiveAward, LedgerKind, Payment, PaymentPurpose, Payout, PayoutStatus, PromoCode,
                     PromoRedemption, ProviderLedgerEntry, Wallet, WalletTransaction, WalletTxKind)

INSTANT_PAYOUT_FEE = 10_000          # ₦100
MIN_PAYOUT_AMOUNT = 100_000          # ₦1,000


# --------------------------------------------------------------------------- wallet
def ensure_wallet(user) -> Wallet:
    wallet, _ = Wallet.objects.get_or_create(user=user)
    return wallet


@transaction.atomic
def wallet_post(user, kind: str, amount: int, ride=None, note: str = "", reference: str = "", allow_negative: bool = False) -> WalletTransaction:
    """Add a signed amount to the user's wallet. Debits fail with `insufficient_wallet` unless allow_negative."""
    wallet = Wallet.objects.select_for_update().get(pk=ensure_wallet(user).pk)
    new_balance = wallet.balance_amount + amount
    if amount < 0 and new_balance < 0 and not allow_negative:
        raise ApiError("insufficient_wallet", "Your wallet balance is too low.", status_code=402,
                       details={"balance_amount": wallet.balance_amount, "required_amount": -amount})
    wallet.balance_amount = new_balance
    wallet.save(update_fields=["balance_amount", "updated_at"])
    return WalletTransaction.objects.create(wallet=wallet, kind=kind, amount=amount, balance_after=new_balance,
                                            ride=ride, note=note, reference=reference)


# --------------------------------------------------------------------------- promos
def validate_promo(user, code: str, service: str) -> PromoCode:
    """Return the promo or raise a specific, user-readable error."""
    now = timezone.now()
    promo = PromoCode.objects.filter(code=code.upper().strip()).first()
    if not promo or not promo.is_active:
        raise ApiError("promo_invalid", "This promo code isn't valid.", details={"promo_code": ["Code not found."]})
    if now < promo.valid_from:
        raise ApiError("promo_not_started", f"This code starts on {promo.valid_from:%d %b}.")
    if now > promo.valid_to:
        raise ApiError("promo_expired", f"This code ended on {promo.valid_to:%d %b}.")
    if promo.service and promo.service != service:
        raise ApiError("promo_wrong_service", f"This code only works on {promo.get_service_display().lower()} rides.")
    if promo.budget_amount is not None and promo.spent_amount >= promo.budget_amount:
        raise ApiError("promo_exhausted", "This promo has ended.")
    if promo.usage_limit is not None and promo.redemptions.count() >= promo.usage_limit:
        raise ApiError("promo_exhausted", "This promo has ended.")
    if promo.redemptions.filter(user=user).count() >= promo.per_user_limit:
        raise ApiError("promo_used", "You've already used this code.")
    if promo.first_ride_only:
        from apps.rides.models import Ride
        if Ride.objects.filter(customer=user, status="completed").exists():
            raise ApiError("promo_first_ride_only", "This code is for your first ride only.")
    return promo


def promo_discount(promo: PromoCode, gross: int) -> int:
    if promo.discount_type == "percent":
        amount = int(Decimal(gross) * Decimal(promo.value) / 100)
        if promo.max_discount_amount:
            amount = min(amount, promo.max_discount_amount)
    else:
        amount = promo.value
    return max(0, min(amount, gross))


# --------------------------------------------------------------------------- ledger
def ledger(provider, kind: str, amount: int, ride=None, payout=None, note: str = "") -> ProviderLedgerEntry:
    return ProviderLedgerEntry.objects.create(provider=provider, kind=kind, amount=amount, ride=ride, payout=payout, note=note)


def provider_balance(provider) -> int:
    return ProviderLedgerEntry.objects.filter(provider=provider).aggregate(s=Sum("amount"))["s"] or 0


def commission_for(ride) -> int:
    from apps.pricing.services import active_rule
    pct = active_rule(ride.ride_type, ride.city).commission_percent
    return int((Decimal(ride.gross_amount) * pct / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# --------------------------------------------------------------------------- ride settlement
def _charge(ride, purpose: str, amount: int) -> Payment:
    """Charge the ride's card or wallet. Returns the Payment (status succeeded/failed)."""
    user = ride.customer
    if ride.payment_method == "wallet":
        try:
            wallet_post(user, WalletTxKind.RIDE_PAYMENT if purpose == PaymentPurpose.RIDE else WalletTxKind.TIP, -amount, ride=ride)
            return Payment.objects.create(user=user, ride=ride, purpose=purpose, method="wallet", amount=amount, status="succeeded")
        except ApiError as exc:
            return Payment.objects.create(user=user, ride=ride, purpose=purpose, method="wallet", amount=amount, status="failed",
                                          failure_reason=str(exc.detail))
    if ride.payment_method == "card":
        token = ride.card.gateway_token if ride.card else ""
        result = get_gateway().charge(token, amount, user.email)
        return Payment.objects.create(user=user, ride=ride, purpose=purpose, method="card", amount=amount,
                                      status="succeeded" if result.ok else "failed", gateway_reference=result.reference,
                                      failure_reason=result.failure_reason)
    raise ValueError("cash is not charged through the gateway")


@transaction.atomic
def settle_completed_ride(ride) -> None:
    """
    Called once when the provider completes the trip.
    * provider ledger: +gross fare, -commission
    * promo redemption recorded (platform funds the discount)
    * card/wallet: charge now; cash: wait for POST /provider/trips/{id}/collect-cash/
    """
    ride.commission_amount = commission_for(ride)
    ledger(ride.provider, LedgerKind.FARE, ride.gross_amount, ride=ride)
    ledger(ride.provider, LedgerKind.COMMISSION, -ride.commission_amount, ride=ride)
    if ride.quote and ride.quote.promo and ride.discount_amount:
        PromoRedemption.objects.get_or_create(promo=ride.quote.promo, ride=ride,
                                              defaults={"user": ride.customer, "discount_amount": ride.discount_amount})
        PromoCode.objects.filter(pk=ride.quote.promo_id).update(spent_amount=F("spent_amount") + ride.discount_amount)
    if ride.payment_method in ("card", "wallet"):
        payment = _charge(ride, PaymentPurpose.RIDE, ride.total_amount)
        ride.payment_status = "paid" if payment.status == "succeeded" else "failed"
    ride.save(update_fields=["commission_amount", "payment_status", "updated_at"])
    award_incentives(ride.provider)


@transaction.atomic
def collect_cash(ride) -> None:
    """Provider confirms they received the cash (trip total + tip)."""
    if ride.payment_method != "cash":
        raise Conflict("not_cash_ride", "This ride isn't paid in cash.")
    if ride.cash_collected_at:
        return
    ride.cash_collected_at = timezone.now()
    ride.payment_status = "paid"
    ride.save(update_fields=["cash_collected_at", "payment_status", "updated_at"])
    ledger(ride.provider, LedgerKind.CASH_COLLECTED, -ride.cash_due_amount, ride=ride, note="Cash kept by provider")
    Payment.objects.create(user=ride.customer, ride=ride, purpose=PaymentPurpose.RIDE, method="cash",
                           amount=ride.cash_due_amount, status="succeeded")


@transaction.atomic
def retry_payment(ride, method: str, card=None) -> None:
    """Customer fixes a failed card/wallet payment ('Payment failed' screen): try card, wallet or switch to cash."""
    if ride.payment_status != "failed":
        raise Conflict("payment_not_failed", "This ride doesn't have a failed payment.")
    ride.payment_method = method
    ride.card = card
    if method == "cash":
        ride.payment_status = "pending"   # provider now collects cash
        ride.save(update_fields=["payment_method", "card", "payment_status", "updated_at"])
        return
    payment = _charge(ride, PaymentPurpose.RIDE, ride.total_amount)
    ride.payment_status = "paid" if payment.status == "succeeded" else "failed"
    ride.save(update_fields=["payment_method", "card", "payment_status", "updated_at"])
    if ride.payment_status == "failed":
        raise ApiError("payment_failed", payment.failure_reason or "Payment didn't go through.", status_code=402)


@transaction.atomic
def add_tip(ride, amount: int) -> None:
    """Tip goes 100% to the provider. Cash rides: added to the cash to collect (if not collected yet)."""
    if ride.status != "completed":
        raise Conflict("invalid_state", "You can tip after the trip is completed.")
    if ride.tip_amount:
        raise Conflict("already_tipped", "You've already tipped for this trip.")
    if amount <= 0:
        raise ApiError("invalid_amount", "Tip must be more than ₦0.")
    if ride.payment_method == "cash":
        if ride.cash_collected_at:
            raise Conflict("cash_already_collected", "Cash was already collected. Hand the tip to your driver/rider directly.")
    else:
        payment = _charge(ride, PaymentPurpose.TIP, amount)
        if payment.status != "succeeded":
            raise ApiError("payment_failed", payment.failure_reason or "Tip payment failed.", status_code=402)
    ride.tip_amount = amount
    ride.save(update_fields=["tip_amount", "updated_at"])
    ledger(ride.provider, LedgerKind.TIP, amount, ride=ride)


@transaction.atomic
def charge_cancellation_fee(ride) -> None:
    """Customer cancelled after the grace period: charge the fee, credit the provider."""
    fee = ride.cancellation_fee_amount
    if not fee:
        return
    if ride.payment_method == "card":
        _charge(ride, PaymentPurpose.CANCELLATION_FEE, fee)
    else:
        # wallet and cash rides: debit the wallet, which may go negative (settled on next top-up)
        wallet_post(ride.customer, WalletTxKind.CANCELLATION_FEE, -fee, ride=ride, allow_negative=True)
    if ride.provider:
        ledger(ride.provider, LedgerKind.CANCELLATION_FEE, fee, ride=ride)


@transaction.atomic
def refund_ride(ride, amount: int, reason: str, claw_back: bool = False) -> None:
    """
    Staff refund. Cash rides refund to the wallet; card rides back to the card; wallet rides to the wallet.
    `claw_back=True` also deducts the amount from the provider (e.g. confirmed overcharge).
    """
    refundable = ride.total_amount + ride.tip_amount - ride.refunded_amount
    if amount <= 0 or amount > refundable:
        raise ApiError("invalid_amount", "Refund must be between ₦0.01 and the amount paid.", details={"max_amount": refundable})
    if ride.payment_method == "card":
        get_gateway().refund(reference=str(ride.id), amount=amount)
        Payment.objects.create(user=ride.customer, ride=ride, purpose=PaymentPurpose.REFUND, method="card", amount=-amount, status="succeeded")
    else:
        wallet_post(ride.customer, WalletTxKind.REFUND, amount, ride=ride, note=reason)
    ride.refunded_amount += amount
    ride.payment_status = "refunded" if ride.refunded_amount >= ride.total_amount + ride.tip_amount else "partially_refunded"
    ride.save(update_fields=["refunded_amount", "payment_status", "updated_at"])
    if claw_back and ride.provider:
        ledger(ride.provider, LedgerKind.ADJUSTMENT, -amount, ride=ride, note=f"Refund clawback: {reason}")


# --------------------------------------------------------------------------- earnings + payouts
def earnings_summary(provider, period: str) -> dict:
    """Numbers for the Earnings screen. period: day | week | month."""
    now = timezone.localtime()
    if period == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    entries = ProviderLedgerEntry.objects.filter(provider=provider, created_at__gte=start)
    by_kind = {row["kind"]: row["s"] for row in entries.values("kind").annotate(s=Sum("amount"))}
    fares = by_kind.get(LedgerKind.FARE, 0)
    tips = by_kind.get(LedgerKind.TIP, 0)
    incentives = by_kind.get(LedgerKind.INCENTIVE, 0)
    fees = by_kind.get(LedgerKind.CANCELLATION_FEE, 0)
    commission = -by_kind.get(LedgerKind.COMMISSION, 0)
    from apps.rides.models import Ride
    rides = Ride.objects.filter(provider=provider, status="completed", completed_at__gte=start)
    daily = rides.annotate(day=TruncDate("completed_at")).values("day").annotate(n=Count("id"), s=Sum("gross_amount")).order_by("day")
    return {
        "period": period,
        "starts_at": start,
        "trips": rides.count(),
        "fares_amount": fares,
        "tips_amount": tips,
        "incentives_amount": incentives,
        "cancellation_fees_amount": fees,
        "commission_amount": commission,
        "net_amount": fares + tips + incentives + fees - commission,
        "cash_collected_amount": -by_kind.get(LedgerKind.CASH_COLLECTED, 0),
        "balance_amount": provider_balance(provider),
        "by_day": [{"date": str(d["day"]), "trips": d["n"], "gross_amount": d["s"] or 0} for d in daily],
    }


def _bank_snapshot(provider) -> dict:
    if not (provider.bank_name and provider.bank_account_number and provider.bank_account_name):
        raise Conflict("bank_details_missing", "Add your bank details before cashing out.")
    return {"bank_name": provider.bank_name, "bank_account_number": provider.bank_account_number,
            "bank_account_name": provider.bank_account_name}


@transaction.atomic
def request_instant_payout(provider) -> Payout:
    from apps.providers.models import ProviderProfile
    provider = ProviderProfile.objects.select_for_update().get(pk=provider.pk)   # serialise payouts per provider
    balance = provider_balance(provider)
    if balance - INSTANT_PAYOUT_FEE < MIN_PAYOUT_AMOUNT:
        raise Conflict("balance_too_low", "You need at least ₦1,000 (plus the ₦100 fee) to cash out.", details={"balance_amount": balance})
    amount = balance - INSTANT_PAYOUT_FEE
    payout = Payout.objects.create(provider=provider, amount=amount, fee_amount=INSTANT_PAYOUT_FEE, method="instant", **_bank_snapshot(provider))
    ledger(provider, LedgerKind.PAYOUT, -amount, payout=payout)
    ledger(provider, LedgerKind.PAYOUT_FEE, -INSTANT_PAYOUT_FEE, payout=payout)
    return payout


@transaction.atomic
def run_weekly_payouts(staff_user) -> list[Payout]:
    """Create a payout for every provider with a positive balance and bank details (Super Admin > Payouts > Run)."""
    from apps.providers.models import ProviderProfile
    created = []
    for provider in ProviderProfile.objects.select_for_update().filter(status__in=["approved", "suspended"]):
        balance = provider_balance(provider)
        if balance < MIN_PAYOUT_AMOUNT:
            continue
        try:
            bank = _bank_snapshot(provider)
        except Conflict:
            continue
        payout = Payout.objects.create(provider=provider, amount=balance, method="scheduled", processed_by=staff_user, **bank)
        ledger(provider, LedgerKind.PAYOUT, -balance, payout=payout)
        created.append(payout)
    return created


@transaction.atomic
def mark_payout(payout: Payout, status: str, staff_user, reason: str = "") -> Payout:
    """Staff (or the bank webhook) marks a payout paid/failed. Failed payouts return money to the balance."""
    if payout.status in (PayoutStatus.PAID, PayoutStatus.FAILED):
        raise Conflict("invalid_state", f"Payout is already {payout.status}.")
    payout.status = status
    payout.processed_by = staff_user
    if status == PayoutStatus.PAID:
        payout.paid_at = timezone.now()
    elif status == PayoutStatus.FAILED:
        payout.failure_reason = reason or "Bank rejected the transfer."
        ledger(payout.provider, LedgerKind.PAYOUT_REVERSAL, payout.amount + payout.fee_amount, payout=payout, note=payout.failure_reason)
    payout.save()
    return payout


# --------------------------------------------------------------------------- incentives
def incentive_progress(provider) -> list[dict]:
    from apps.rides.models import Ride
    now = timezone.now()
    out = []
    for inc in Incentive.objects.filter(service=provider.service, is_active=True, ends_at__gte=now - timedelta(days=7)):
        trips = Ride.objects.filter(provider=provider, status="completed", completed_at__range=(inc.starts_at, inc.ends_at)).count()
        award = inc.awards.filter(provider=provider).first()
        out.append({"id": str(inc.id), "name": inc.name, "description": inc.description, "target_trips": inc.target_trips,
                    "completed_trips": min(trips, inc.target_trips), "reward_amount": inc.reward_amount,
                    "starts_at": inc.starts_at, "ends_at": inc.ends_at, "achieved": bool(award),
                    "status": "achieved" if award else ("ended" if inc.ends_at < now else "active")})
    return out


def award_incentives(provider) -> None:
    """Called after each completed trip: pay any incentive whose target was just reached."""
    from apps.rides.models import Ride
    now = timezone.now()
    for inc in Incentive.objects.filter(service=provider.service, is_active=True, starts_at__lte=now, ends_at__gte=now):
        if inc.awards.filter(provider=provider).exists() or provider.acceptance_rate < inc.min_acceptance_rate:
            continue
        trips = Ride.objects.filter(provider=provider, status="completed", completed_at__range=(inc.starts_at, inc.ends_at)).count()
        if trips >= inc.target_trips:
            IncentiveAward.objects.create(incentive=inc, provider=provider, amount=inc.reward_amount)
            ledger(provider, LedgerKind.INCENTIVE, inc.reward_amount, note=inc.name)
