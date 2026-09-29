"""
Super Admin > Trips and Dispatch.

    GET  /api/v1/staff/rides/?service=&status=&payment_method=&date_from=&date_to=&search=&page=
    GET  /api/v1/staff/rides/{id}/                       full timeline (events) + dispatch attempts (offers)
    POST /api/v1/staff/rides/{id}/refund/                {amount, reason, claw_back}
    POST /api/v1/staff/rides/{id}/cancel/                {reason}

    GET  /api/v1/staff/dispatch/?service=                rides still searching (manual-dispatch ones first)
    GET  /api/v1/staff/dispatch/{ride_id}/candidates/    nearest eligible drivers/riders
    POST /api/v1/staff/dispatch/{ride_id}/assign/        {provider_id} -> sends them a 60 s offer
"""
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.schema import errors
from apps.providers.models import ProviderProfile
from apps.rides import dispatch
from apps.rides.models import PaymentMethodKind, Ride, RideStatus

from .. import services
from ..serializers import (AssignSerializer, CandidateSerializer, ReasonSerializer, RefundSerializer, StaffOfferSerializer,
                           StaffRideDetailSerializer, StaffRideRowSerializer)
from ._common import (SERVICE_PARAM, CancelRoles, TAG, OperationsOnly, RefundRoles, RidesRead,
                      service_filter)


def _ride(pk) -> Ride:
    return get_object_or_404(Ride.objects.select_related("customer", "provider__user", "vehicle", "ride_type"), pk=pk)


def _ride_detail(request, ride) -> Response:
    ride = _ride(ride.pk)   # reload with relations after an action
    return Response(StaffRideDetailSerializer(ride, context={"request": request}).data)


# =========================================================================== trips
class RideListView(generics.ListAPIView):
    permission_classes = [RidesRead]
    serializer_class = StaffRideRowSerializer
    filter_backends = []

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Ride.objects.none()
        p = self.request.query_params
        qs = Ride.objects.select_related("ride_type", "customer", "provider__user").order_by("-requested_at")
        service = service_filter(self.request)
        if service:
            qs = qs.filter(service=service)
        if p.get("status"):
            qs = qs.filter(status__in=p["status"].split(","))   # comma-separated, e.g. accepted,arrived
        if p.get("payment_method"):
            qs = qs.filter(payment_method=p["payment_method"])
        if p.get("customer"):
            qs = qs.filter(customer_id=p["customer"])
        if p.get("provider"):
            qs = qs.filter(provider_id=p["provider"])
        if p.get("date_from") and parse_date(p["date_from"]):
            qs = qs.filter(requested_at__date__gte=parse_date(p["date_from"]))
        if p.get("date_to") and parse_date(p["date_to"]):
            qs = qs.filter(requested_at__date__lte=parse_date(p["date_to"]))
        if p.get("search"):
            s = p["search"].strip()
            qs = qs.filter(Q(customer__phone__icontains=s.lstrip("0")) | Q(provider__user__phone__icontains=s.lstrip("0"))
                           | Q(pickup_address__icontains=s) | Q(dropoff_address__icontains=s) | Q(vehicle__plate_number__icontains=s))
        return qs

    @extend_schema(
        tags=[TAG], summary="Trips",
        description=("**GET /api/v1/staff/rides/** · Bearer (operations, support, finance). Paginated, newest first.\n\n"
                     "`status` accepts several values separated by commas. Dates are YYYY-MM-DD (Lagos time). "
                     "Search matches customer/driver phone, addresses or plate."),
        parameters=[SERVICE_PARAM, OpenApiParameter("status", str, description=f"One or more of: {', '.join(RideStatus.values)}"),
                    OpenApiParameter("payment_method", str, enum=PaymentMethodKind.values),
                    OpenApiParameter("customer", str, description="User id"), OpenApiParameter("provider", str, description="Provider id"),
                    OpenApiParameter("date_from", str), OpenApiParameter("date_to", str), OpenApiParameter("search", str),
                    OpenApiParameter("page", int)],
        responses={200: StaffRideRowSerializer(many=True), **errors(400, 401, 403)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class RideDetailView(APIView):
    permission_classes = [RidesRead]

    @extend_schema(tags=[TAG], summary="Trip detail",
                   description=("**GET /api/v1/staff/rides/{id}/** · Bearer (operations, support, finance). "
                                "People, route, money breakdown, ratings, `events` (timeline) and `offers` (who was asked)."),
                   responses={200: StaffRideDetailSerializer, **errors(401, 403, 404)})
    def get(self, request, pk):
        return _ride_detail(request, _ride(pk))


class RideRefundView(APIView):
    permission_classes = [RefundRoles]

    @extend_schema(
        tags=[TAG], summary="Refund a trip",
        description=("**POST /api/v1/staff/rides/{id}/refund/** · Bearer (finance, support).\n\n"
                     "Card rides refund to the card; cash and wallet rides refund to the customer's wallet. "
                     "`claw_back` also deducts it from the driver/rider (confirmed overcharge). "
                     "Support staff can refund up to ₦5,000 per call. 400 `invalid_amount` with `details.max_amount`."),
        request=RefundSerializer, responses={200: StaffRideDetailSerializer, **errors(400, 401, 403, 404, 409)},
    )
    def post(self, request, pk):
        data = RefundSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        return _ride_detail(request, services.refund(request, _ride(pk), v["amount"], v["reason"], v["claw_back"]))


class RideCancelView(APIView):
    permission_classes = [CancelRoles]

    @extend_schema(tags=[TAG], summary="Cancel a trip (staff)",
                   description=("**POST /api/v1/staff/rides/{id}/cancel/** · Bearer (operations). For stuck or unsafe trips "
                                "before pickup. The customer isn't charged. Trips in progress can't be cancelled (409) — "
                                "let them complete, then refund."),
                   request=ReasonSerializer, responses={200: StaffRideDetailSerializer, **errors(400, 401, 403, 404, 409)})
    def post(self, request, pk):
        data = ReasonSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return _ride_detail(request, services.cancel_ride(request, _ride(pk), data.validated_data["reason"]))


# =========================================================================== dispatch
class DispatchQueueView(APIView):
    permission_classes = [OperationsOnly]

    @extend_schema(
        tags=[TAG], summary="Dispatch queue",
        description=("**GET /api/v1/staff/dispatch/?service=** · Bearer (operations). Rides still searching; the ones "
                     "flagged `needs_manual_dispatch` (3 offers missed or nobody nearby) come first. Poll every 5 s."),
        parameters=[SERVICE_PARAM], responses={200: StaffRideRowSerializer(many=True), **errors(400, 401, 403)},
    )
    def get(self, request):
        dispatch.tick()   # keep offers/timeouts fresh even without the background worker
        qs = (Ride.objects.filter(status=RideStatus.SEARCHING).select_related("ride_type", "customer", "provider__user")
              .order_by("-needs_manual_dispatch", "requested_at"))
        service = service_filter(request)
        if service:
            qs = qs.filter(service=service)
        return Response(StaffRideRowSerializer(qs[:100], many=True).data)


class DispatchCandidatesView(APIView):
    permission_classes = [OperationsOnly]

    @extend_schema(tags=[TAG], summary="Who can take this ride",
                   description=("**GET /api/v1/staff/dispatch/{ride_id}/candidates/** · Bearer (operations). Nearest eligible "
                                "drivers/riders (online, approved, free, right vehicle, within the dispatch radius) who "
                                "haven't been offered this ride yet."),
                   responses={200: CandidateSerializer(many=True), **errors(401, 403, 404)})
    def get(self, request, pk):
        ride = _ride(pk)
        rows = []
        for provider, km in dispatch.find_candidates(ride, limit=20):
            v = provider.vehicles.filter(is_active=True).first()
            rows.append({"provider_id": provider.id, "name": provider.user.full_name, "phone": provider.user.phone,
                         "distance_km": round(km, 2), "rating_avg": provider.rating_avg,
                         "acceptance_rate": provider.acceptance_rate,
                         "vehicle": f"{v.color} {v.make} {v.model} · {v.plate_number}" if v else None})
        return Response(CandidateSerializer(rows, many=True).data)


class DispatchAssignView(APIView):
    permission_classes = [OperationsOnly]

    @extend_schema(tags=[TAG], summary="Assign a ride manually",
                   description=("**POST /api/v1/staff/dispatch/{ride_id}/assign/** · Bearer (operations). Withdraws any "
                                "pending offer and sends this driver/rider a 60 s offer (they still have to accept). "
                                "409 if the ride is no longer searching; 400 `provider_not_eligible`."),
                   request=AssignSerializer, responses={201: StaffOfferSerializer, **errors(400, 401, 403, 404, 409)})
    def post(self, request, pk):
        data = AssignSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        provider = get_object_or_404(ProviderProfile, pk=data.validated_data["provider_id"])
        offer = services.assign_ride(request, _ride(pk), provider)
        return Response(StaffOfferSerializer(offer).data, status=201)
