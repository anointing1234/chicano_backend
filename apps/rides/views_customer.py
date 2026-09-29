"""
Booking endpoints for the User app · Cars and User app · Bikes.

    POST /api/v1/rides/estimate/          upfront fares for every ride type of a service
    POST /api/v1/rides/                   book at a quoted fare
    GET  /api/v1/rides/                   history (?status=&service=)
    GET  /api/v1/rides/active/            the current ride, if any (call on app start to restore state)
    GET  /api/v1/rides/{id}/              one ride; poll every 3-5 s while is_active
    POST /api/v1/rides/{id}/cancel/
    POST /api/v1/rides/{id}/rate/         stars + tags + comment (+ optional tip)
    POST /api/v1/rides/{id}/tip/
    POST /api/v1/rides/{id}/retry-payment/   fix a failed card/wallet payment
    POST /api/v1/rides/{id}/sos/          emergency (also works without an active ride: see /support/sos/)
"""
from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import ApiError
from apps.core.permissions import IsCustomer
from apps.core.schema import errors
from apps.payments.models import PaymentMethod
from apps.pricing.serializers import FareQuoteSerializer
from apps.pricing.services import create_quotes
from apps.support.serializers import SOSAlertSerializer, SOSRequestSerializer
from apps.support.services import raise_sos

from . import dispatch, services
from .models import ACTIVE_STATUSES, RatingDirection, Ride
from .serializers import (CancelSerializer, EstimateRequestSerializer, RateSerializer, RatingSerializer, RetryPaymentSerializer,
                          RideListSerializer, RideRequestSerializer, RideSerializer, TipSerializer)


def _my_ride(request, pk) -> Ride:
    """Customers can only see their own rides (404 otherwise, never 403: don't leak existence)."""
    return get_object_or_404(Ride.objects.select_related("provider__user", "vehicle", "ride_type"), pk=pk, customer=request.user)


class EstimateView(APIView):
    permission_classes = [IsCustomer]

    @extend_schema(
        tags=["Booking"], summary="Get upfront fares",
        description=("**POST /api/v1/rides/estimate/** · Bearer (customer).\n\n"
                     "Returns one quote per ride type (cars: Standard, XL, Premium; bikes: Bike). Each quote is locked for "
                     "10 minutes; book with its `quote_id`. Amounts in kobo.\n\n"
                     "Errors: `outside_service_zone` (bikes restricted areas), `promo_*` (invalid promo), `pricing_unavailable` (409)."),
        request=EstimateRequestSerializer,
        responses={200: FareQuoteSerializer(many=True), **errors(400, 401, 403, 409)},
        examples=[OpenApiExample("Car trip", request_only=True, value={
            "service": "car", "pickup": {"lat": 6.4474, "lng": 3.4553}, "dropoff": {"lat": 6.6139, "lng": 3.3581},
            "stops": [], "promo_code": "WELCOME10"})],
    )
    def post(self, request):
        data = EstimateRequestSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        quotes = create_quotes(request.user, v["service"], (v["pickup"]["lat"], v["pickup"]["lng"]),
                               (v["dropoff"]["lat"], v["dropoff"]["lng"]),
                               [{"lat": float(s["lat"]), "lng": float(s["lng"]), "address": s["address"]} for s in v.get("stops", [])],
                               v.get("promo_code") or None)
        return Response(FareQuoteSerializer(quotes, many=True).data)


class RideListCreateView(generics.ListAPIView):
    permission_classes = [IsCustomer]
    serializer_class = RideListSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Ride.objects.none()
        qs = Ride.objects.filter(customer=self.request.user).select_related("ride_type")
        if s := self.request.query_params.get("status"):
            qs = qs.filter(status__in=s.split(","))
        if svc := self.request.query_params.get("service"):
            qs = qs.filter(service=svc)
        return qs

    @extend_schema(tags=["Booking"], summary="My rides (history)",
                   description=("**GET /api/v1/rides/?status=completed,cancelled&service=bike&page=1** · Bearer (customer). "
                                "Paginated. Upcoming tab: `status=scheduled`."),
                   parameters=[OpenApiParameter("status", str, description="Comma-separated statuses"),
                               OpenApiParameter("service", str, enum=["car", "bike"])],
                   responses={200: RideListSerializer(many=True), **errors(401, 403)})
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        tags=["Booking"], summary="Book a ride",
        description=("**POST /api/v1/rides/** · Bearer (customer).\n\n"
                     "Creates the ride at the quoted price and starts finding a driver/rider (status `searching`), "
                     "or `scheduled` if `scheduled_for` is set. Then poll GET /rides/{id}/.\n\n"
                     "Errors: 404 `quote_not_found`; 409 `quote_expired`, `quote_used`, `active_ride_exists`; "
                     "400 `card_required`; 402 `insufficient_wallet`."),
        request=RideRequestSerializer,
        responses={201: RideSerializer, **errors(400, 401, 402, 403, 404, 409)},
        examples=[OpenApiExample("Cash car ride", request_only=True, value={
            "quote_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6", "payment_method": "cash",
            "pickup_address": "14 Admiralty Way, Lekki Phase 1", "dropoff_address": "Ikeja City Mall",
            "pickup_note": "Blue gate, opposite GTBank"})],
    )
    def post(self, request):
        data = RideRequestSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        card = None
        if v["payment_method"] == "card" and v.get("payment_method_id"):
            card = PaymentMethod.objects.filter(pk=v["payment_method_id"], user=request.user).first()
            if not card:
                raise ApiError("card_not_found", "That card isn't saved on your account.", status_code=404)
        ride = services.request_ride(request.user, quote_id=v["quote_id"], payment_method=v["payment_method"], card=card,
                                     pickup_address=v["pickup_address"], dropoff_address=v["dropoff_address"],
                                     pickup_note=v.get("pickup_note", ""), stop_addresses=v.get("stop_addresses"),
                                     scheduled_for=v.get("scheduled_for"))
        return Response(RideSerializer(ride, context={"request": request}).data, status=status.HTTP_201_CREATED)


class ActiveRideView(APIView):
    permission_classes = [IsCustomer]

    @extend_schema(tags=["Booking"], summary="My current ride",
                   description=("**GET /api/v1/rides/active/** · Bearer (customer). Call on app launch: if a ride is active, "
                                "open the trip screen. 404 `no_active_ride` otherwise."),
                   responses={200: RideSerializer, **errors(401, 403, 404)})
    def get(self, request):
        dispatch.tick()
        ride = Ride.objects.filter(customer=request.user, status__in=ACTIVE_STATUSES).first()
        if not ride:
            raise ApiError("no_active_ride", "No active ride.", status_code=404)
        return Response(RideSerializer(ride, context={"request": request}).data)


class RideDetailView(APIView):
    permission_classes = [IsCustomer]

    @extend_schema(tags=["Booking"], summary="Ride details (poll for live updates)",
                   description=("**GET /api/v1/rides/{id}/** · Bearer (customer).\n\n"
                                "Poll every 3-5 s while `is_active`. Drive the UI from `status`: "
                                "searching → accepted (show provider, vehicle, `eta_to_pickup_seconds`, `provider_location`) → "
                                "arrived → in_progress → completed (rate + tip) | cancelled | no_provider."),
                   responses={200: RideSerializer, **errors(401, 403, 404)})
    def get(self, request, pk):
        dispatch.tick()   # keeps matching moving even without a background worker
        return Response(RideSerializer(_my_ride(request, pk), context={"request": request}).data)


class CancelRideView(APIView):
    permission_classes = [IsCustomer]

    @extend_schema(tags=["Booking"], summary="Cancel my ride",
                   description=("**POST /api/v1/rides/{id}/cancel/** · Bearer (customer).\n\n"
                                "Free while searching and during the grace period after a driver/rider accepts; then "
                                "`cancellation_fee_if_cancelled_now` (shown in GET /rides/{id}/) is charged. "
                                "409 `invalid_state` once the trip has started."),
                   request=CancelSerializer, responses={200: RideSerializer, **errors(400, 401, 403, 404, 409)})
    def post(self, request, pk):
        data = CancelSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        ride = services.cancel_by_customer(_my_ride(request, pk), data.validated_data["reason"])
        return Response(RideSerializer(ride, context={"request": request}).data)


class RateRideView(APIView):
    permission_classes = [IsCustomer]

    @extend_schema(tags=["Booking"], summary="Rate my driver/rider (and tip)",
                   description=("**POST /api/v1/rides/{id}/rate/** · Bearer (customer). Once per ride. Optional `tip_amount` "
                                "(kobo) goes 100% to the driver/rider; on cash rides it's added to the cash to collect. "
                                "409 `already_rated`, `invalid_state`."),
                   request=RateSerializer, responses={201: RatingSerializer, **errors(400, 401, 402, 403, 404, 409)},
                   examples=[OpenApiExample("5 stars + ₦500 tip", request_only=True,
                                            value={"stars": 5, "tags": ["Smooth driving", "Clean car"], "comment": "", "tip_amount": 50000})])
    def post(self, request, pk):
        data = RateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        ride = _my_ride(request, pk)
        v = data.validated_data
        with transaction.atomic():   # rating and tip succeed or fail together
            rating = services.rate(ride, RatingDirection.CUSTOMER_TO_PROVIDER, v["stars"], v["tags"], v["comment"])
            if v.get("tip_amount"):
                from apps.payments.services import add_tip
                add_tip(ride, v["tip_amount"])
        return Response(RatingSerializer(rating).data, status=status.HTTP_201_CREATED)


class TipView(APIView):
    permission_classes = [IsCustomer]

    @extend_schema(tags=["Booking"], summary="Tip my driver/rider",
                   description="**POST /api/v1/rides/{id}/tip/** · Bearer (customer). Once per ride. 409 `already_tipped`, `cash_already_collected`; 402 `payment_failed`.",
                   request=TipSerializer, responses={200: RideSerializer, **errors(400, 401, 402, 403, 404, 409)})
    def post(self, request, pk):
        data = TipSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        from apps.payments.services import add_tip
        ride = _my_ride(request, pk)
        add_tip(ride, data.validated_data["amount"])
        return Response(RideSerializer(ride, context={"request": request}).data)


class RetryPaymentView(APIView):
    permission_classes = [IsCustomer]

    @extend_schema(tags=["Booking"], summary="Fix a failed payment",
                   description=("**POST /api/v1/rides/{id}/retry-payment/** · Bearer (customer). For the 'Card payment didn't go "
                                "through' screen: try another card, the wallet, or switch to cash (the driver/rider then collects)."),
                   request=RetryPaymentSerializer, responses={200: RideSerializer, **errors(400, 401, 402, 403, 404, 409)})
    def post(self, request, pk):
        data = RetryPaymentSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        ride = _my_ride(request, pk)
        card = None
        if data.validated_data["payment_method"] == "card":
            card = PaymentMethod.objects.filter(pk=data.validated_data.get("payment_method_id"), user=request.user).first()
            if not card:
                raise ApiError("card_not_found", "Choose a saved card.", status_code=404)
        from apps.payments.services import retry_payment
        retry_payment(ride, data.validated_data["payment_method"], card)
        return Response(RideSerializer(ride, context={"request": request}).data)


class RideSOSView(APIView):
    permission_classes = [IsCustomer]

    @extend_schema(tags=["Support & safety"], summary="SOS during a ride",
                   description=("**POST /api/v1/rides/{id}/sos/** · Bearer (customer). Alerts the safety team and SMSes the "
                                "customer's emergency contacts with the live trip link. The app should ALSO offer 'Call 112' "
                                "(works without data)."),
                   request=SOSRequestSerializer, responses={201: SOSAlertSerializer, **errors(401, 403, 404)})
    def post(self, request, pk):
        data = SOSRequestSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        alert = raise_sos(request.user, _my_ride(request, pk), data.validated_data.get("lat"), data.validated_data.get("lng"))
        return Response(SOSAlertSerializer(alert).data, status=status.HTTP_201_CREATED)
