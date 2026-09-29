"""
Super Admin > Support and Safety.

    GET   /api/v1/staff/tickets/?status=&category=&priority=&assigned=me&page=
    GET   /api/v1/staff/tickets/{id}/                   includes internal notes
    PATCH /api/v1/staff/tickets/{id}/                   {status, priority, assigned_to}
    POST  /api/v1/staff/tickets/{id}/reply/             {body, internal, status}

    GET   /api/v1/staff/sos/?status=open|acknowledged|resolved|active&service=&page=
    POST  /api/v1/staff/sos/{id}/acknowledge/           {notes}
    POST  /api/v1/staff/sos/{id}/resolve/               {notes}

A public reply (internal=false) pushes a notification to the customer/driver/rider;
internal notes are only ever visible here.
"""
from django.db.models import Case, IntegerField, When
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audit import log_action
from apps.core.schema import errors
from apps.support.models import SOSAlert, SOSStatus, SupportTicket, TicketCategory, TicketStatus

from .. import services
from ..serializers import (SOSActionSerializer, StaffSOSSerializer, StaffTicketReplySerializer, StaffTicketSerializer,
                           StaffTicketUpdateSerializer)
from ._common import SERVICE_PARAM, TAG, SupportRoles, service_filter


def _ticket(pk) -> SupportTicket:
    return get_object_or_404(SupportTicket.objects.select_related("user", "assigned_to"), pk=pk)


# =========================================================================== tickets
class TicketListView(generics.ListAPIView):
    permission_classes = [SupportRoles]
    serializer_class = StaffTicketSerializer
    filter_backends = []

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return SupportTicket.objects.none()
        p = self.request.query_params
        qs = SupportTicket.objects.select_related("user", "assigned_to").prefetch_related("messages__sender")
        if p.get("status"):
            qs = qs.filter(status__in=p["status"].split(","))
        if p.get("category"):
            qs = qs.filter(category=p["category"])
        if p.get("priority"):
            qs = qs.filter(priority=p["priority"])
        if p.get("assigned") == "me":
            qs = qs.filter(assigned_to=self.request.user)
        elif p.get("assigned") == "none":
            qs = qs.filter(assigned_to__isnull=True)
        # Urgent first, then the ticket that has waited longest.
        rank = Case(When(priority="urgent", then=0), When(priority="high", then=1), When(priority="normal", then=2),
                    default=3, output_field=IntegerField())
        return qs.annotate(priority_rank=rank).order_by("priority_rank", "updated_at")

    @extend_schema(
        tags=[TAG], summary="Support tickets",
        description=("**GET /api/v1/staff/tickets/** · Bearer (support, operations). Paginated: urgent first, then longest-waiting. "
                     "`status` accepts comma-separated values, e.g. `open,pending`."),
        parameters=[OpenApiParameter("status", str, description=", ".join(TicketStatus.values)),
                    OpenApiParameter("category", str, enum=TicketCategory.values),
                    OpenApiParameter("priority", str, enum=["low", "normal", "high", "urgent"]),
                    OpenApiParameter("assigned", str, enum=["me", "none"]), OpenApiParameter("page", int)],
        responses={200: StaffTicketSerializer(many=True), **errors(401, 403)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class TicketDetailView(APIView):
    permission_classes = [SupportRoles]

    @extend_schema(tags=[TAG], summary="Ticket detail",
                   description="**GET /api/v1/staff/tickets/{id}/** · Bearer (support, operations). All messages incl. internal notes.",
                   responses={200: StaffTicketSerializer, **errors(401, 403, 404)})
    def get(self, request, pk):
        return Response(StaffTicketSerializer(_ticket(pk), context={"request": request}).data)

    @extend_schema(tags=[TAG], summary="Update a ticket",
                   description="**PATCH /api/v1/staff/tickets/{id}/** · Bearer (support, operations). Change status, priority or assignee.",
                   request=StaffTicketUpdateSerializer, responses={200: StaffTicketSerializer, **errors(400, 401, 403, 404)})
    def patch(self, request, pk):
        ticket = _ticket(pk)
        data = StaffTicketUpdateSerializer(ticket, data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        data.save()
        log_action(request, "ticket.update", ticket, {"changed": list(data.validated_data.keys())})
        return Response(StaffTicketSerializer(_ticket(pk), context={"request": request}).data)


class TicketReplyView(APIView):
    permission_classes = [SupportRoles]

    @extend_schema(tags=[TAG], summary="Reply or add an internal note",
                   description=("**POST /api/v1/staff/tickets/{id}/reply/** · Bearer (support, operations). "
                                "`internal=false` sends the message to the person (push + in-app); `internal=true` is a staff note. "
                                "A public reply sets status to `pending` (waiting for them) unless you pass `status`. "
                                "Unassigned tickets are assigned to you."),
                   request=StaffTicketReplySerializer, responses={200: StaffTicketSerializer, **errors(400, 401, 403, 404)})
    def post(self, request, pk):
        data = StaffTicketReplySerializer(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        services.reply_ticket(request, _ticket(pk), v["body"], v["internal"], v.get("status"))
        return Response(StaffTicketSerializer(_ticket(pk), context={"request": request}).data)


# =========================================================================== SOS
class SOSListView(generics.ListAPIView):
    permission_classes = [SupportRoles]
    serializer_class = StaffSOSSerializer
    filter_backends = []

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return SOSAlert.objects.none()
        qs = SOSAlert.objects.select_related("raised_by", "ride", "handled_by").order_by("-created_at")
        status_value = self.request.query_params.get("status", "active")
        if status_value == "active":
            qs = qs.exclude(status=SOSStatus.RESOLVED)
        elif status_value != "all":
            qs = qs.filter(status=status_value)
        service = service_filter(self.request)
        if service:
            qs = qs.filter(ride__service=service)
        return qs

    @extend_schema(tags=[TAG], summary="SOS alerts",
                   description=("**GET /api/v1/staff/sos/** · Bearer (support, operations). Defaults to `status=active` "
                                "(open + acknowledged). Poll every 5 s; show a loud banner while any are open."),
                   parameters=[OpenApiParameter("status", str, enum=SOSStatus.values + ["active", "all"], default="active"),
                               SERVICE_PARAM, OpenApiParameter("page", int)],
                   responses={200: StaffSOSSerializer(many=True), **errors(400, 401, 403)})
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class _SOSActionView(APIView):
    permission_classes = [SupportRoles]
    to_status = SOSStatus.ACKNOWLEDGED

    def post(self, request, pk):
        data = SOSActionSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        alert = services.update_sos(request, get_object_or_404(SOSAlert, pk=pk), self.to_status, data.validated_data["notes"])
        return Response(StaffSOSSerializer(alert).data)


class SOSAcknowledgeView(_SOSActionView):
    to_status = SOSStatus.ACKNOWLEDGED

    @extend_schema(tags=[TAG], summary="Acknowledge an SOS",
                   description="**POST /api/v1/staff/sos/{id}/acknowledge/** · Bearer (support, operations). Tells the person help is coming.",
                   request=SOSActionSerializer, responses={200: StaffSOSSerializer, **errors(400, 401, 403, 404, 409)})
    def post(self, request, pk):
        return super().post(request, pk)


class SOSResolveView(_SOSActionView):
    to_status = SOSStatus.RESOLVED

    @extend_schema(tags=[TAG], summary="Resolve an SOS",
                   description="**POST /api/v1/staff/sos/{id}/resolve/** · Bearer (support, operations). Add what happened in `notes`.",
                   request=SOSActionSerializer, responses={200: StaffSOSSerializer, **errors(400, 401, 403, 404, 409)})
    def post(self, request, pk):
        return super().post(request, pk)
