"""
Wallet & payments (User apps) and earnings & payouts (Driver/Rider apps).

User apps:
    GET  /api/v1/wallet/                          balance
    GET  /api/v1/wallet/transactions/             history (paginated)
    POST /api/v1/wallet/top-up/                   charge a saved card into the wallet
    GET/POST /api/v1/wallet/payment-methods/      saved cards
    DELETE   /api/v1/wallet/payment-methods/{id}/
    POST /api/v1/wallet/promos/validate/          check a code before estimating

Driver/Rider apps:
    GET  /api/v1/provider/earnings/summary/?period=day|week|month
    GET  /api/v1/provider/earnings/ledger/        every credit/debit (paginated)
    GET  /api/v1/provider/payouts/                payout history
    POST /api/v1/provider/payouts/instant/        cash out now (₦100 fee)
    GET  /api/v1/provider/incentives/             bonus progress
"""
from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import ApiError
from apps.core.permissions import IsApprovedProvider, IsCustomer, IsProvider
from apps.core.schema import errors

from . import services
from .gateway import get_gateway
from .models import Payment, PaymentMethod, WalletTransaction, WalletTxKind
from .serializers import (AddCardSerializer, EarningsSummarySerializer, IncentiveProgressSerializer, LedgerEntrySerializer,
                          PaymentMethodSerializer, PayoutSerializer, PromoPublicSerializer, PromoValidateSerializer,
                          TopUpSerializer, WalletSerializer, WalletTransactionSerializer)


# =========================================================================== customer
class WalletView(APIView):
    permission_classes = [IsCustomer]

    @extend_schema(tags=["Wallet & payments"], summary="Wallet balance",
                   description="**GET /api/v1/wallet/** · Bearer (customer). `balance_amount` in kobo; can be negative if a cancellation fee is owed.",
                   responses={200: WalletSerializer, **errors(401, 403)})
    def get(self, request):
        return Response(WalletSerializer(services.ensure_wallet(request.user)).data)


class WalletTransactionsView(generics.ListAPIView):
    permission_classes = [IsCustomer]
    serializer_class = WalletTransactionSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return WalletTransaction.objects.none()
        return WalletTransaction.objects.filter(wallet__user=self.request.user)

    @extend_schema(tags=["Wallet & payments"], summary="Wallet history",
                   description="**GET /api/v1/wallet/transactions/** · Bearer (customer). Paginated.",
                   responses={200: WalletTransactionSerializer(many=True), **errors(401, 403)})
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class TopUpView(APIView):
    permission_classes = [IsCustomer]

    @extend_schema(tags=["Wallet & payments"], summary="Top up the wallet",
                   description="**POST /api/v1/wallet/top-up/** · Bearer (customer). Charges a saved card. 402 `payment_failed` if declined.",
                   request=TopUpSerializer, responses={200: WalletSerializer, **errors(400, 401, 402, 403, 404)})
    def post(self, request):
        data = TopUpSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        card = get_object_or_404(PaymentMethod, pk=data.validated_data["payment_method_id"], user=request.user)
        amount = data.validated_data["amount"]
        result = get_gateway().charge(card.gateway_token, amount, request.user.email)
        Payment.objects.create(user=request.user, purpose="topup", method="card", amount=amount,
                               status="succeeded" if result.ok else "failed", gateway_reference=result.reference,
                               failure_reason=result.failure_reason)
        if not result.ok:
            raise ApiError("payment_failed", result.failure_reason or "Your card was declined.", status_code=402)
        services.wallet_post(request.user, WalletTxKind.TOPUP, amount, reference=result.reference)
        return Response(WalletSerializer(services.ensure_wallet(request.user)).data)


class PaymentMethodListView(APIView):
    permission_classes = [IsCustomer]

    @extend_schema(tags=["Wallet & payments"], summary="My saved cards",
                   description="**GET /api/v1/wallet/payment-methods/** · Bearer (customer). Cash and wallet are always available and not listed here.",
                   responses={200: PaymentMethodSerializer(many=True), **errors(401, 403)})
    def get(self, request):
        return Response(PaymentMethodSerializer(request.user.payment_methods.order_by("-is_default", "-created_at"), many=True).data)

    @extend_schema(tags=["Wallet & payments"], summary="Save a card",
                   description=("**POST /api/v1/wallet/payment-methods/** · Bearer (customer).\n\n"
                                "Tokenise the card in the app with the gateway (Paystack) first and send the token + display details. "
                                "Card numbers and CVV must never reach this API."),
                   request=AddCardSerializer, responses={201: PaymentMethodSerializer, **errors(400, 401, 403)})
    def post(self, request):
        data = AddCardSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        with transaction.atomic():
            make_default = v.pop("make_default") or not request.user.payment_methods.exists()
            if make_default:
                request.user.payment_methods.update(is_default=False)
            card = PaymentMethod.objects.create(user=request.user, gateway=get_gateway().name, is_default=make_default, **v)
        return Response(PaymentMethodSerializer(card).data, status=status.HTTP_201_CREATED)


class PaymentMethodDeleteView(APIView):
    permission_classes = [IsCustomer]

    @extend_schema(tags=["Wallet & payments"], summary="Remove a saved card",
                   description="**DELETE /api/v1/wallet/payment-methods/{id}/** · Bearer (customer).",
                   responses={204: None, **errors(401, 403, 404)})
    def delete(self, request, pk):
        get_object_or_404(PaymentMethod, pk=pk, user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PromoValidateView(APIView):
    permission_classes = [IsCustomer]

    @extend_schema(tags=["Wallet & payments"], summary="Check a promo code",
                   description=("**POST /api/v1/wallet/promos/validate/** · Bearer (customer). Returns the promo if usable; "
                                "otherwise 400 with a specific code: promo_invalid, promo_expired, promo_not_started, promo_used, "
                                "promo_wrong_service, promo_exhausted, promo_first_ride_only. Then pass `promo_code` to /rides/estimate/."),
                   request=PromoValidateSerializer, responses={200: PromoPublicSerializer, **errors(400, 401, 403)})
    def post(self, request):
        data = PromoValidateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        promo = services.validate_promo(request.user, data.validated_data["code"], data.validated_data["service"])
        return Response(PromoPublicSerializer(promo).data)


# =========================================================================== provider
class EarningsSummaryView(APIView):
    permission_classes = [IsProvider]

    @extend_schema(tags=["Provider earnings"], summary="Earnings summary",
                   description=("**GET /api/v1/provider/earnings/summary/?period=day|week|month** · Bearer (driver/rider).\n\n"
                                "Powers the Earnings screen: totals, bar chart (`by_day`) and the current balance."),
                   parameters=[OpenApiParameter("period", str, enum=["day", "week", "month"], default="week")],
                   responses={200: EarningsSummarySerializer, **errors(401, 403)})
    def get(self, request):
        period = request.query_params.get("period", "week")
        if period not in ("day", "week", "month"):
            raise ApiError("validation_error", "period must be day, week or month.", details={"period": ["Invalid value."]})
        return Response(EarningsSummarySerializer(services.earnings_summary(request.user.provider_profile, period)).data)


class LedgerView(generics.ListAPIView):
    permission_classes = [IsProvider]
    serializer_class = LedgerEntrySerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            from .models import ProviderLedgerEntry
            return ProviderLedgerEntry.objects.none()
        return self.request.user.provider_profile.ledger.all()

    @extend_schema(tags=["Provider earnings"], summary="Earnings ledger",
                   description="**GET /api/v1/provider/earnings/ledger/** · Bearer (driver/rider). Every credit/debit (fare, commission, tip, cash collected, payouts). Paginated.",
                   responses={200: LedgerEntrySerializer(many=True), **errors(401, 403)})
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class PayoutListView(APIView):
    permission_classes = [IsProvider]

    @extend_schema(tags=["Provider earnings"], summary="My payouts",
                   description="**GET /api/v1/provider/payouts/** · Bearer (driver/rider).",
                   responses={200: PayoutSerializer(many=True), **errors(401, 403)})
    def get(self, request):
        return Response(PayoutSerializer(request.user.provider_profile.payouts.all()[:50], many=True).data)


class InstantPayoutView(APIView):
    permission_classes = [IsApprovedProvider]

    @extend_schema(tags=["Provider earnings"], summary="Cash out now",
                   description=("**POST /api/v1/provider/payouts/instant/** · Bearer (approved driver/rider). Sends the whole balance "
                                "minus a ₦100 fee. 409 `balance_too_low`, `bank_details_missing`."),
                   request=None, responses={201: PayoutSerializer, **errors(401, 403, 409)})
    def post(self, request):
        return Response(PayoutSerializer(services.request_instant_payout(request.user.provider_profile)).data, status=status.HTTP_201_CREATED)


class IncentivesView(APIView):
    permission_classes = [IsProvider]

    @extend_schema(tags=["Provider earnings"], summary="My incentives",
                   description="**GET /api/v1/provider/incentives/** · Bearer (driver/rider). Active and recent bonuses with progress.",
                   responses={200: IncentiveProgressSerializer(many=True), **errors(401, 403)})
    def get(self, request):
        return Response(IncentiveProgressSerializer(services.incentive_progress(request.user.provider_profile), many=True).data)
