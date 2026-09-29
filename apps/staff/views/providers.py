"""
Super Admin > Drivers & riders, Documents review, Vehicle inspection.

Drivers (cars) and riders (bikes) are both `ProviderProfile` rows on top of the combined
users table; `service` tells them apart and every list takes ?service=car|bike.

    GET  /api/v1/staff/providers/?service=&status=&online=&search=&page=
    GET  /api/v1/staff/providers/{id}/
    POST /api/v1/staff/providers/{id}/approve/
    POST /api/v1/staff/providers/{id}/reject/          {reason}
    POST /api/v1/staff/providers/{id}/suspend/         {reason}
    POST /api/v1/staff/providers/{id}/reinstate/

    POST /api/v1/staff/vehicles/{id}/approve/          {ride_types: ["car_standard", ...]}
    POST /api/v1/staff/vehicles/{id}/reject/           {reason}

    GET  /api/v1/staff/documents/?status=pending&service=&doc_type=&page=
    POST /api/v1/staff/documents/{id}/approve/
    POST /api/v1/staff/documents/{id}/reject/          {reason}   (reason is shown in the app)
"""
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.schema import errors
from apps.providers.models import (DocumentStatus, DocumentType, ProviderDocument, ProviderProfile,
                                   ProviderStatus, Vehicle)
from apps.providers.serializers import VehicleSerializer

from .. import services
from ..serializers import (ReasonSerializer, StaffDocumentSerializer, StaffProviderDetailSerializer,
                           StaffProviderRowSerializer, VehicleApproveSerializer)
from ._common import SERVICE_PARAM, TAG, ComplianceOnly, ProvidersRead, SuspendRoles, service_filter



def _provider(pk) -> ProviderProfile:
    return get_object_or_404(ProviderProfile.objects.select_related("user"), pk=pk)


def _detail(request, provider):
    provider.refresh_from_db()
    return Response(StaffProviderDetailSerializer(provider, context={"request": request}).data)


# =========================================================================== providers
class ProviderListView(generics.ListAPIView):
    permission_classes = [ProvidersRead]
    serializer_class = StaffProviderRowSerializer
    filter_backends = []

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return ProviderProfile.objects.none()
        qs = (ProviderProfile.objects.select_related("user")
              .annotate(documents_pending=Count("documents", filter=Q(documents__status=DocumentStatus.PENDING)))
              .order_by("-created_at"))
        p = self.request.query_params
        service = service_filter(self.request)
        if service:
            qs = qs.filter(service=service)
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("online") in ("true", "false"):
            qs = qs.filter(is_online=p["online"] == "true")
        if p.get("search"):
            s = p["search"].strip()
            qs = qs.filter(Q(user__phone__icontains=s.lstrip("0")) | Q(user__first_name__icontains=s) | Q(user__last_name__icontains=s)
                           | Q(vehicles__plate_number__icontains=s)).distinct()
        return qs

    @extend_schema(
        tags=[TAG], summary="Drivers & riders",
        description=("**GET /api/v1/staff/providers/** · Bearer (operations, compliance, support). Paginated.\n\n"
                     "Use `status=under_review` for the approvals queue. Search matches phone, name or plate number."),
        parameters=[SERVICE_PARAM, OpenApiParameter("status", str, enum=ProviderStatus.values),
                    OpenApiParameter("online", bool), OpenApiParameter("search", str), OpenApiParameter("page", int)],
        responses={200: StaffProviderRowSerializer(many=True), **errors(400, 401, 403)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class ProviderDetailView(APIView):
    permission_classes = [ProvidersRead]

    @extend_schema(tags=[TAG], summary="Driver/rider detail",
                   description=("**GET /api/v1/staff/providers/{id}/** · Bearer (operations, compliance, support). "
                                "Vehicles, documents, bank details, balance and the onboarding checklist."),
                   responses={200: StaffProviderDetailSerializer, **errors(401, 403, 404)})
    def get(self, request, pk):
        return _detail(request, _provider(pk))


class ProviderApproveView(APIView):
    permission_classes = [ComplianceOnly]

    @extend_schema(
        tags=[TAG], summary="Approve a driver/rider",
        description=("**POST /api/v1/staff/providers/{id}/approve/** · Bearer (compliance). No body.\n\n"
                     "Needs every required document approved (cars: driver's licence, insurance, registration; bikes: "
                     "rider's licence, insurance, registration) and at least one approved vehicle. Otherwise 409 "
                     "`not_ready` with `details.missing_documents` / `details.vehicle_approved`. "
                     "On success they can go online and receive a push."),
        request=None, responses={200: StaffProviderDetailSerializer, **errors(401, 403, 404, 409)},
    )
    def post(self, request, pk):
        return _detail(request, services.approve_provider(request, _provider(pk)))


class ProviderRejectView(APIView):
    permission_classes = [ComplianceOnly]

    @extend_schema(tags=[TAG], summary="Reject an application",
                   description="**POST /api/v1/staff/providers/{id}/reject/** · Bearer (compliance). The reason is shown in the app.",
                   request=ReasonSerializer, responses={200: StaffProviderDetailSerializer, **errors(400, 401, 403, 404, 409)})
    def post(self, request, pk):
        data = ReasonSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return _detail(request, services.reject_provider(request, _provider(pk), data.validated_data["reason"]))


class ProviderSuspendView(APIView):
    permission_classes = [SuspendRoles]

    @extend_schema(tags=[TAG], summary="Suspend a driver/rider",
                   description=("**POST /api/v1/staff/providers/{id}/suspend/** · Bearer (operations, compliance). Takes them "
                                "offline; they can't go online until reinstated. Their customer account (if any) is unaffected — "
                                "use /staff/users/{id}/suspend/ to block the person everywhere."),
                   request=ReasonSerializer, responses={200: StaffProviderDetailSerializer, **errors(400, 401, 403, 404, 409)})
    def post(self, request, pk):
        data = ReasonSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return _detail(request, services.suspend_provider(request, _provider(pk), data.validated_data["reason"]))


class ProviderReinstateView(APIView):
    permission_classes = [SuspendRoles]

    @extend_schema(tags=[TAG], summary="Reinstate a driver/rider",
                   description=("**POST /api/v1/staff/providers/{id}/reinstate/** · Bearer (operations, compliance). "
                                "Back to approved if they were approved before, otherwise back to under review."),
                   request=None, responses={200: StaffProviderDetailSerializer, **errors(401, 403, 404, 409)})
    def post(self, request, pk):
        return _detail(request, services.reinstate_provider(request, _provider(pk)))


# =========================================================================== vehicles
class VehicleApproveView(APIView):
    permission_classes = [ComplianceOnly]

    @extend_schema(
        tags=[TAG], summary="Approve a vehicle / bike",
        description=("**POST /api/v1/staff/vehicles/{id}/approve/** · Bearer (compliance).\n\n"
                     "Choose which ride types it may serve. Each must match the vehicle kind (car/bike) and the vehicle "
                     "year must meet the ride type's minimum. Bikes need both helmets recorded."),
        request=VehicleApproveSerializer, responses={200: VehicleSerializer, **errors(400, 401, 403, 404)},
    )
    def post(self, request, pk):
        vehicle = get_object_or_404(Vehicle, pk=pk)
        data = VehicleApproveSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return Response(VehicleSerializer(services.approve_vehicle(request, vehicle, data.validated_data["ride_types"])).data)


class VehicleRejectView(APIView):
    permission_classes = [ComplianceOnly]

    @extend_schema(tags=[TAG], summary="Reject a vehicle / bike",
                   description="**POST /api/v1/staff/vehicles/{id}/reject/** · Bearer (compliance). The driver/rider gets a push with the reason.",
                   request=ReasonSerializer, responses={200: VehicleSerializer, **errors(400, 401, 403, 404)})
    def post(self, request, pk):
        vehicle = get_object_or_404(Vehicle.objects.select_related("provider__user"), pk=pk)
        data = ReasonSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return Response(VehicleSerializer(services.reject_vehicle(request, vehicle, data.validated_data["reason"])).data)


# =========================================================================== documents
class DocumentListView(generics.ListAPIView):
    permission_classes = [ComplianceOnly]
    serializer_class = StaffDocumentSerializer
    filter_backends = []

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return ProviderDocument.objects.none()
        p = self.request.query_params
        qs = ProviderDocument.objects.select_related("provider__user", "reviewed_by")
        status_value = p.get("status", "pending")
        if status_value != "all":
            qs = qs.filter(status=status_value)
        service = service_filter(self.request)
        if service:
            qs = qs.filter(provider__service=service)
        if p.get("doc_type"):
            qs = qs.filter(doc_type=p["doc_type"])
        if p.get("provider"):
            qs = qs.filter(provider_id=p["provider"])
        # Oldest first for the review queue, newest first otherwise.
        return qs.order_by("created_at" if status_value == "pending" else "-created_at")

    @extend_schema(
        tags=[TAG], summary="Documents review queue",
        description=("**GET /api/v1/staff/documents/** · Bearer (compliance). Paginated. Defaults to `status=pending`, "
                     "oldest first. `file` / `back_file` are full URLs to open or preview."),
        parameters=[OpenApiParameter("status", str, enum=DocumentStatus.values + ["all"], default="pending"), SERVICE_PARAM,
                    OpenApiParameter("doc_type", str, enum=DocumentType.values), OpenApiParameter("provider", str),
                    OpenApiParameter("page", int)],
        responses={200: StaffDocumentSerializer(many=True), **errors(400, 401, 403)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class DocumentApproveView(APIView):
    permission_classes = [ComplianceOnly]

    @extend_schema(tags=[TAG], summary="Approve a document",
                   description=("**POST /api/v1/staff/documents/{id}/approve/** · Bearer (compliance). No body. "
                                "Approving the last required document does NOT approve the driver/rider automatically — "
                                "call /staff/providers/{id}/approve/ after checking the vehicle."),
                   request=None, responses={200: StaffDocumentSerializer, **errors(401, 403, 404, 409)})
    def post(self, request, pk):
        doc = services.approve_document(request, get_object_or_404(ProviderDocument, pk=pk))
        return Response(StaffDocumentSerializer(doc, context={"request": request}).data)


class DocumentRejectView(APIView):
    permission_classes = [ComplianceOnly]

    @extend_schema(tags=[TAG], summary="Reject a document",
                   description=("**POST /api/v1/staff/documents/{id}/reject/** · Bearer (compliance). The reason is shown "
                                "to the driver/rider in the Documents screen (e.g. 'Photo is blurry, retake in daylight')."),
                   request=ReasonSerializer, responses={200: StaffDocumentSerializer, **errors(400, 401, 403, 404)})
    def post(self, request, pk):
        doc = get_object_or_404(ProviderDocument.objects.select_related("provider__user"), pk=pk)
        data = ReasonSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        doc = services.reject_document(request, doc, data.validated_data["reason"])
        return Response(StaffDocumentSerializer(doc, context={"request": request}).data)
