"""
Super Admin > Payouts (finance team).

    GET  /api/v1/staff/payouts/?status=&service=&method=&page=
    POST /api/v1/staff/payouts/run/                  create this week's payouts for everyone owed ≥ ₦1,000
    POST /api/v1/staff/payouts/{id}/mark-paid/       {reference}
    POST /api/v1/staff/payouts/{id}/mark-failed/     {reason}   money goes back to their balance

Flow: run -> payouts are `pending` -> finance sends the bank transfers (or the Paystack
Transfers integration does) -> mark each paid / failed.
"""
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.schema import errors
from apps.payments.models import Payout, PayoutStatus

from .. import services
from ..serializers import MarkPaidSerializer, ReasonSerializer, RunPayoutsResultSerializer, StaffPayoutSerializer
from ._common import SERVICE_PARAM, TAG, FinanceOnly, service_filter


class PayoutListView(generics.ListAPIView):
    permission_classes = [FinanceOnly]
    serializer_class = StaffPayoutSerializer
    filter_backends = []

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Payout.objects.none()
        p = self.request.query_params
        qs = Payout.objects.select_related("provider__user", "processed_by").order_by("-created_at")
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("method"):
            qs = qs.filter(method=p["method"])
        service = service_filter(self.request)
        if service:
            qs = qs.filter(provider__service=service)
        return qs

    @extend_schema(tags=[TAG], summary="Payouts",
                   description="**GET /api/v1/staff/payouts/** · Bearer (finance). Paginated, newest first.",
                   parameters=[OpenApiParameter("status", str, enum=PayoutStatus.values), SERVICE_PARAM,
                               OpenApiParameter("method", str, enum=["scheduled", "instant"]), OpenApiParameter("page", int)],
                   responses={200: StaffPayoutSerializer(many=True), **errors(400, 401, 403)})
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class PayoutRunView(APIView):
    permission_classes = [FinanceOnly]

    @extend_schema(tags=[TAG], summary="Run weekly payouts",
                   description=("**POST /api/v1/staff/payouts/run/** · Bearer (finance). No body. Creates a pending payout for "
                                "every driver/rider with a balance of at least ₦1,000 and bank details on file. "
                                "Safe to run twice: the second run finds zero balances."),
                   request=None, responses={201: RunPayoutsResultSerializer, **errors(401, 403)})
    def post(self, request):
        created = services.run_payouts(request)
        total = sum(p.amount for p in created)
        return Response(RunPayoutsResultSerializer({"created": len(created), "total_amount": total, "payouts": created}).data, status=201)


class PayoutMarkPaidView(APIView):
    permission_classes = [FinanceOnly]

    @extend_schema(tags=[TAG], summary="Mark a payout paid",
                   description="**POST /api/v1/staff/payouts/{id}/mark-paid/** · Bearer (finance). 409 if already paid/failed.",
                   request=MarkPaidSerializer, responses={200: StaffPayoutSerializer, **errors(400, 401, 403, 404, 409)})
    def post(self, request, pk):
        data = MarkPaidSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        payout = services.mark_payout_paid(request, get_object_or_404(Payout, pk=pk), data.validated_data["reference"])
        return Response(StaffPayoutSerializer(payout).data)


class PayoutMarkFailedView(APIView):
    permission_classes = [FinanceOnly]

    @extend_schema(tags=[TAG], summary="Mark a payout failed",
                   description=("**POST /api/v1/staff/payouts/{id}/mark-failed/** · Bearer (finance). The amount (and any "
                                "instant fee) returns to their balance. Ask them to check bank details."),
                   request=ReasonSerializer, responses={200: StaffPayoutSerializer, **errors(400, 401, 403, 404, 409)})
    def post(self, request, pk):
        data = ReasonSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        payout = services.mark_payout_failed(request, get_object_or_404(Payout, pk=pk), data.validated_data["reason"])
        return Response(StaffPayoutSerializer(payout).data)
