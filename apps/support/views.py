"""
Support & safety endpoints (all four apps).

    GET  /api/v1/support/notifications/              inbox (paginated)
    POST /api/v1/support/notifications/read-all/
    GET/POST /api/v1/support/tickets/                my tickets / open a ticket
    GET  /api/v1/support/tickets/{id}/               ticket with messages
    POST /api/v1/support/tickets/{id}/reply/
    GET/POST /api/v1/support/lost-items/             report something left in a car/on a bike trip
    POST /api/v1/support/sos/                        SOS without a ride (customer side)
    GET  /api/v1/support/share/{token}/              PUBLIC: live trip for shared links (no auth)
"""
from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import ApiError
from apps.core.permissions import IsCustomer
from apps.core.schema import errors

from . import services
from .models import LostItemReport, Notification, SupportTicket, TicketMessage
from .serializers import (LostItemSerializer, NotificationSerializer, PublicTripSerializer, SOSAlertSerializer,
                          SOSRequestSerializer, TicketCreateSerializer, TicketReplySerializer, TicketSerializer)


class NotificationListView(generics.ListAPIView):
    serializer_class = NotificationSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Notification.objects.none()
        return Notification.objects.filter(user=self.request.user)

    @extend_schema(tags=["Support & safety"], summary="My notifications",
                   description="**GET /api/v1/support/notifications/** · Bearer. Paginated inbox. Use `data.type` + ids to deep-link.",
                   responses={200: NotificationSerializer(many=True), **errors(401)})
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class NotificationReadAllView(APIView):
    @extend_schema(tags=["Support & safety"], summary="Mark all notifications read",
                   description="**POST /api/v1/support/notifications/read-all/** · Bearer. Returns 204.",
                   request=None, responses={204: None, **errors(401)})
    def post(self, request):
        services.mark_all_read(request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class TicketListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["Support & safety"], summary="My support tickets",
                   description="**GET /api/v1/support/tickets/** · Bearer (any app).",
                   responses={200: TicketSerializer(many=True), **errors(401)})
    def get(self, request):
        return Response(TicketSerializer(SupportTicket.objects.filter(user=request.user), many=True).data)

    @extend_schema(tags=["Support & safety"], summary="Open a ticket",
                   description=("**POST /api/v1/support/tickets/** · Bearer (any app). Link `ride` when the problem is about a trip "
                                "(the 'Get help with this trip' button)."),
                   request=TicketCreateSerializer, responses={201: TicketSerializer, **errors(400, 401)})
    def post(self, request):
        data = TicketCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        ride = data.validated_data.get("ride")
        if ride and request.user not in (ride.customer, getattr(ride.provider, "user", None)):
            raise ApiError("not_found", "We couldn't find that trip.", status_code=404)
        with transaction.atomic():
            message = data.validated_data.pop("message")
            ticket = SupportTicket.objects.create(user=request.user, **data.validated_data,
                                                  priority="urgent" if data.validated_data["category"] == "safety" else "normal")
            TicketMessage.objects.create(ticket=ticket, sender=request.user, body=message)
        return Response(TicketSerializer(ticket).data, status=status.HTTP_201_CREATED)


class TicketDetailView(APIView):
    @extend_schema(tags=["Support & safety"], summary="Ticket with messages",
                   description="**GET /api/v1/support/tickets/{id}/** · Bearer (owner).",
                   responses={200: TicketSerializer, **errors(401, 404)})
    def get(self, request, pk):
        return Response(TicketSerializer(get_object_or_404(SupportTicket, pk=pk, user=request.user)).data)


class TicketReplyView(APIView):
    @extend_schema(tags=["Support & safety"], summary="Reply to my ticket",
                   description="**POST /api/v1/support/tickets/{id}/reply/** · Bearer (owner). Re-opens resolved tickets.",
                   request=TicketReplySerializer, responses={200: TicketSerializer, **errors(400, 401, 404)})
    def post(self, request, pk):
        ticket = get_object_or_404(SupportTicket, pk=pk, user=request.user)
        data = TicketReplySerializer(data=request.data)
        data.is_valid(raise_exception=True)
        TicketMessage.objects.create(ticket=ticket, sender=request.user, body=data.validated_data["body"])
        ticket.status = "open"
        ticket.save(update_fields=["status", "updated_at"])
        return Response(TicketSerializer(ticket).data)


class LostItemView(APIView):
    permission_classes = [IsCustomer]

    @extend_schema(tags=["Support & safety"], summary="My lost-item reports",
                   description="**GET /api/v1/support/lost-items/** · Bearer (customer).",
                   responses={200: LostItemSerializer(many=True), **errors(401, 403)})
    def get(self, request):
        return Response(LostItemSerializer(LostItemReport.objects.filter(customer=request.user), many=True).data)

    @extend_schema(tags=["Support & safety"], summary="Report a lost item",
                   description=("**POST /api/v1/support/lost-items/** · Bearer (customer). Creates a ticket and notifies the "
                                "driver/rider (they call back through a masked number). Response includes `ticket_id`."),
                   request=LostItemSerializer, responses={201: LostItemSerializer, **errors(400, 401, 403, 404)})
    def post(self, request):
        data = LostItemSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        ride = data.validated_data["ride"]
        if ride.customer_id != request.user.id:
            raise ApiError("not_found", "We couldn't find that trip.", status_code=404)
        with transaction.atomic():
            ticket = SupportTicket.objects.create(user=request.user, ride=ride, category="lost_item", subject=f"Lost {data.validated_data['category']}")
            TicketMessage.objects.create(ticket=ticket, sender=request.user, body=data.validated_data["description"])
            report = data.save(customer=request.user, ticket=ticket)
        if ride.provider:
            title, lead = ("Package issue reported", "The sender reports") if ride.service == "bike" else ("Lost item reported", "A customer left")
            services.notify(ride.provider.user, title, f"{lead}: {report.description}",
                            data={"type": "lost_item", "ride_id": str(ride.id)})
        return Response(LostItemSerializer(report).data, status=status.HTTP_201_CREATED)


class SOSView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["Support & safety"], summary="SOS (no trip attached)",
                   description="**POST /api/v1/support/sos/** · Bearer. Prefer POST /rides/{id}/sos/ during a ride.",
                   request=SOSRequestSerializer, responses={201: SOSAlertSerializer, **errors(400, 401)})
    def post(self, request):
        data = SOSRequestSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        alert = services.raise_sos(request.user, None, data.validated_data.get("lat"), data.validated_data.get("lng"))
        return Response(SOSAlertSerializer(alert).data, status=status.HTTP_201_CREATED)


class PublicTripView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(tags=["Support & safety"], summary="Shared trip (public)",
                   description=("**GET /api/v1/support/share/{token}/** · NO auth. Powers the 'Share trip' link that family "
                                "open in a browser. Shows status, route and live location only while the trip is active."),
                   responses={200: PublicTripSerializer, **errors(404)})
    def get(self, request, token):
        from apps.rides.models import Ride
        ride = get_object_or_404(Ride.objects.select_related("provider__user", "vehicle"), share_token=token)
        p, v = ride.provider, ride.vehicle
        live = ride.is_active and p and p.last_lat is not None
        return Response({
            "status": ride.status, "service": ride.service,
            "pickup_address": ride.pickup_address, "dropoff_address": ride.dropoff_address,
            "provider_first_name": p.user.first_name if p else None,
            "vehicle": f"{v.color} {v.make} {v.model} · {v.plate_number}" if v else None,
            "location": {"lat": p.last_lat, "lng": p.last_lng} if live else None,
            "updated_at": ride.updated_at,
        })
