"""
Trip endpoints for the Driver app · Cars and the Rider app · Bikes.

    GET  /api/v1/provider/offers/current/            poll every 3 s while online (204 = nothing right now)
    POST /api/v1/provider/offers/{id}/accept/
    POST /api/v1/provider/offers/{id}/decline/
    GET  /api/v1/provider/trips/current/             the trip in progress (204 = none)
    GET  /api/v1/provider/trips/                     history
    POST /api/v1/provider/trips/{id}/arrive/
    POST /api/v1/provider/trips/{id}/helmet-check/   bikes only, required before start
    POST /api/v1/provider/trips/{id}/start/
    POST /api/v1/provider/trips/{id}/complete/
    POST /api/v1/provider/trips/{id}/collect-cash/   cash rides: confirm cash received
    POST /api/v1/provider/trips/{id}/helmet-returned/ bikes: passenger returned the helmet
    POST /api/v1/provider/trips/{id}/cancel/         before pickup (ride is re-dispatched, customer not charged)
    POST /api/v1/provider/trips/{id}/no-show/        after the free wait at pickup (customer pays the fee)
    POST /api/v1/provider/trips/{id}/rate/           rate the customer
    POST /api/v1/provider/sos/                       emergency (provider side)
"""
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import Conflict
from apps.core.permissions import IsApprovedProvider
from apps.core.schema import errors
from apps.support.serializers import SOSAlertSerializer, SOSRequestSerializer
from apps.support.services import raise_sos

from . import dispatch, services
from .models import PROVIDER_BUSY_STATUSES, OfferStatus, RatingDirection, Ride, RideEvent, RideOffer
from .serializers import CancelSerializer, OfferSerializer, ProviderTripSerializer, RateSerializer, RatingSerializer


def _my_trip(request, pk) -> Ride:
    return get_object_or_404(Ride, pk=pk, provider=request.user.provider_profile)


def _trip_response(request, ride):
    ride.refresh_from_db()
    return Response(ProviderTripSerializer(ride, context={"request": request}).data)


class CurrentOfferView(APIView):
    permission_classes = [IsApprovedProvider]

    @extend_schema(tags=["Provider trips"], summary="Incoming request (poll)",
                   description=("**GET /api/v1/provider/offers/current/** · Bearer (approved driver/rider).\n\n"
                                "Poll every 3 s while online and not on a trip (a push notification also arrives). "
                                "200 = show the full-screen request card with a countdown from `seconds_left`; "
                                "204 = nothing right now."),
                   responses={200: OfferSerializer, 204: None, **errors(401, 403)})
    def get(self, request):
        dispatch.tick()
        offer = (RideOffer.objects.filter(provider=request.user.provider_profile, status=OfferStatus.SENT, expires_at__gt=timezone.now())
                 .select_related("ride__ride_type", "ride__customer").first())
        if not offer:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(OfferSerializer(offer).data)


class OfferActionView(APIView):
    permission_classes = [IsApprovedProvider]
    offer_action = None   # "accept" | "decline" (set in urls.py)

    @extend_schema(tags=["Provider trips"], summary="Accept or decline a request",
                   description=("**POST /api/v1/provider/offers/{id}/accept/** returns the trip (200) → open navigation to pickup.\n\n"
                                "**POST /api/v1/provider/offers/{id}/decline/** returns 204. Declines and missed requests lower "
                                "the acceptance rate.\n\n409 `offer_expired` if the 15 s window passed or the customer cancelled."),
                   request=None, responses={200: ProviderTripSerializer, 204: None, **errors(401, 403, 404, 409)})
    def post(self, request, pk):
        offer = get_object_or_404(RideOffer, pk=pk, provider=request.user.provider_profile)
        if self.offer_action == "accept":
            if Ride.objects.filter(provider=offer.provider, status__in=PROVIDER_BUSY_STATUSES).exists():
                raise Conflict("already_on_trip", "Finish your current trip first.")
            ride = services.accept_offer(offer)
            return _trip_response(request, ride)
        services.decline_offer(offer)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CurrentTripView(APIView):
    permission_classes = [IsApprovedProvider]

    @extend_schema(tags=["Provider trips"], summary="My current trip",
                   description=("**GET /api/v1/provider/trips/current/** · Bearer (approved driver/rider). Call on app start and "
                                "every 5 s during a trip (to catch customer cancellations). 204 = no active trip."),
                   responses={200: ProviderTripSerializer, 204: None, **errors(401, 403)})
    def get(self, request):
        ride = Ride.objects.filter(provider=request.user.provider_profile, status__in=PROVIDER_BUSY_STATUSES).first()
        if not ride:
            # A cash trip that was completed but not yet collected still needs the "Collect cash" screen.
            ride = Ride.objects.filter(provider=request.user.provider_profile, status="completed", payment_method="cash",
                                       cash_collected_at__isnull=True).first()
        if not ride:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(ProviderTripSerializer(ride, context={"request": request}).data)


class TripHistoryView(generics.ListAPIView):
    permission_classes = [IsApprovedProvider]
    serializer_class = ProviderTripSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Ride.objects.none()
        return Ride.objects.filter(provider=self.request.user.provider_profile).select_related("ride_type", "customer")

    @extend_schema(tags=["Provider trips"], summary="My trip history",
                   description="**GET /api/v1/provider/trips/?page=1** · Bearer (approved driver/rider). Paginated, newest first.",
                   responses={200: ProviderTripSerializer(many=True), **errors(401, 403)})
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


def _trip_action(name: str, doc: str, fn, request_serializer=None):
    """Build a small APIView for a single trip action (keeps the file readable and the docs consistent)."""

    class _View(APIView):
        permission_classes = [IsApprovedProvider]

        @extend_schema(tags=["Provider trips"], summary=doc.split("\n")[0], description=f"**POST /api/v1/provider/trips/{{id}}/{name}/** · Bearer (approved driver/rider).\n\n{doc}",
                       request=request_serializer, responses={200: ProviderTripSerializer, **errors(400, 401, 403, 404, 409)},
                       operation_id=f"provider_trip_{name.replace('-', '_')}")
        def post(self, request, pk):
            ride = _my_trip(request, pk)
            payload = {}
            if request_serializer:
                s = request_serializer(data=request.data)
                s.is_valid(raise_exception=True)
                payload = s.validated_data
            fn(ride, payload, request)
            return _trip_response(request, ride)

    _View.__name__ = f"Trip{name.title().replace('-', '')}View"
    return _View


def _helmet_returned(ride, payload, request):
    if ride.service != "bike":
        raise Conflict("not_bike_ride", "Helmet checks are only for bike trips.")
    ride.helmet_returned_at = timezone.now()
    ride.save(update_fields=["helmet_returned_at", "updated_at"])
    RideEvent.objects.create(ride=ride, event="helmet_returned", actor=request.user)


def _collect_cash(ride, payload, request):
    from apps.payments.services import collect_cash
    if ride.status != "completed":
        raise Conflict("invalid_state", "Complete the trip first.", details={"status": ride.status})
    collect_cash(ride)


ArriveView = _trip_action("arrive", "I've arrived at pickup\nNotifies the customer. Starts the free waiting timer.", lambda r, p, q: services.arrive(r))
HelmetCheckView = _trip_action("helmet-check", "Confirm passenger helmet handed over (bikes)\nRequired before `start` on bike trips (409 `helmet_check_required` otherwise).",
                               lambda r, p, q: services.confirm_helmet(r))
StartView = _trip_action("start", "Start trip\nCustomer is on board. Waiting charges (after the free wait) are added here.", lambda r, p, q: services.start(r))
CompleteView = _trip_action("complete", "Complete trip\nSettles the fare. Cash rides: then show 'Collect ₦X' using `cash_due_amount` and call collect-cash.",
                            lambda r, p, q: services.complete(r))
CollectCashView = _trip_action("collect-cash", "Confirm cash collected\nCash rides only. Records the cash against your earnings balance.", _collect_cash)
HelmetReturnedView = _trip_action("helmet-returned", "Passenger returned the helmet (bikes)", _helmet_returned)
CancelTripView = _trip_action("cancel", "Cancel before pickup\nThe ride is offered to someone else; the customer isn't charged. Counts toward your cancellation rate.",
                              lambda r, p, q: services.cancel_by_provider(r, p.get("reason", "")), CancelSerializer)
NoShowView = _trip_action("no-show", "Customer didn't show up\nAllowed after the free waiting time at pickup; the customer pays the cancellation fee (credited to you).",
                          lambda r, p, q: services.cancel_by_provider(r, "Customer no-show", no_show=True))


class RateCustomerView(APIView):
    permission_classes = [IsApprovedProvider]

    @extend_schema(tags=["Provider trips"], summary="Rate the customer",
                   description="**POST /api/v1/provider/trips/{id}/rate/** · Bearer (approved driver/rider). Once per trip. `tip_amount` is ignored here.",
                   request=RateSerializer, responses={201: RatingSerializer, **errors(400, 401, 403, 404, 409)})
    def post(self, request, pk):
        data = RateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        rating = services.rate(_my_trip(request, pk), RatingDirection.PROVIDER_TO_CUSTOMER, v["stars"], v["tags"], v["comment"])
        return Response(RatingSerializer(rating).data, status=status.HTTP_201_CREATED)


class ProviderSOSView(APIView):
    permission_classes = [IsApprovedProvider]

    @extend_schema(tags=["Support & safety"], summary="SOS (driver/rider)",
                   description="**POST /api/v1/provider/sos/** · Bearer (approved driver/rider). Alerts the safety team; attaches the current trip if any.",
                   request=SOSRequestSerializer, responses={201: SOSAlertSerializer, **errors(400, 401, 403)})
    def post(self, request):
        data = SOSRequestSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        ride = Ride.objects.filter(provider=request.user.provider_profile, status__in=PROVIDER_BUSY_STATUSES).first()
        alert = raise_sos(request.user, ride, data.validated_data.get("lat"), data.validated_data.get("lng"))
        return Response(SOSAlertSerializer(alert).data, status=status.HTTP_201_CREATED)
