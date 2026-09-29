"""
Provider endpoints: Driver app · Cars and Rider app · Bikes (same endpoints; `service` differs).

Onboarding:
    POST  /api/v1/provider/apply/            create driver (service=car) or rider (service=bike) profile
    GET   /api/v1/provider/onboarding/       checklist for the "Your application" screen
    GET   /api/v1/provider/me/               profile + active vehicle + stats
    PATCH /api/v1/provider/me/               bank details, date of birth, city
    GET/POST /api/v1/provider/vehicles/      car or bike details (+ bike safety kit)
    GET/POST /api/v1/provider/documents/     multipart upload of licence, insurance, registration

Availability:
    POST  /api/v1/provider/status/           {is_online}
    POST  /api/v1/provider/location/         {lat, lng, heading} every 4-5 s while online
"""
from django.db import transaction
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsProvider
from apps.core.schema import errors

from . import services
from .models import ProviderDocument
from .serializers import (ApplySerializer, DocumentSerializer, LocationSerializer, OnboardingSerializer,
                          ProviderProfileSerializer, StatusSerializer, VehicleSerializer)


class ApplyView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Provider"], summary="Start a driver or rider application",
        description=("**POST /api/v1/provider/apply/** · Bearer token (right after OTP login).\n\n"
                     "Driver app sends `service=car`; Rider app sends `service=bike`. Idempotent: calling again returns "
                     "the existing profile. 409 `already_provider` if the account is registered for the other service."),
        request=ApplySerializer,
        responses={201: ProviderProfileSerializer, **errors(400, 401, 409)},
        examples=[OpenApiExample("Rider", value={"service": "bike", "city": "Lagos", "first_name": "Ibrahim", "last_name": "Sani"}, request_only=True)],
    )
    def post(self, request):
        data = ApplySerializer(data=request.data)
        data.is_valid(raise_exception=True)
        with transaction.atomic():
            user = request.user
            for f in ("first_name", "last_name"):
                if data.validated_data.get(f):
                    setattr(user, f, data.validated_data[f])
            user.save()
            profile = services.apply(user, data.validated_data["service"], data.validated_data["city"])
        return Response(ProviderProfileSerializer(profile, context={"request": request}).data, status=status.HTTP_201_CREATED)


class OnboardingView(APIView):
    permission_classes = [IsProvider]

    @extend_schema(tags=["Provider"], summary="Application checklist",
                   description=("**GET /api/v1/provider/onboarding/** · Bearer (driver/rider).\n\n"
                                "Drives the 'Your application' screen. Step `state`: done | in_review | action_needed | todo. "
                                "`documents[].rejection_reason` explains what to fix. `can_go_online` becomes true on approval."),
                   responses={200: OnboardingSerializer, **errors(401, 403)})
    def get(self, request):
        return Response(services.onboarding_checklist(request.user.provider_profile))


class ProviderMeView(APIView):
    permission_classes = [IsProvider]

    @extend_schema(tags=["Provider"], summary="My driver/rider profile",
                   description="**GET /api/v1/provider/me/** · Bearer (driver/rider). Includes rating, acceptance and cancellation rates, active vehicle.",
                   responses={200: ProviderProfileSerializer, **errors(401, 403)})
    def get(self, request):
        return Response(ProviderProfileSerializer(request.user.provider_profile, context={"request": request}).data)

    @extend_schema(tags=["Provider"], summary="Update bank details / date of birth / city",
                   description="**PATCH /api/v1/provider/me/** · Bearer (driver/rider).",
                   request=ProviderProfileSerializer, responses={200: ProviderProfileSerializer, **errors(400, 401, 403)})
    def patch(self, request):
        serializer = ProviderProfileSerializer(request.user.provider_profile, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class VehicleView(APIView):
    permission_classes = [IsProvider]

    @extend_schema(tags=["Provider"], summary="List my vehicles",
                   description="**GET /api/v1/provider/vehicles/** · Bearer (driver/rider).",
                   responses={200: VehicleSerializer(many=True), **errors(401, 403)})
    def get(self, request):
        return Response(VehicleSerializer(request.user.provider_profile.vehicles.all(), many=True).data)

    @extend_schema(tags=["Provider"], summary="Add my car or bike",
                   description=("**POST /api/v1/provider/vehicles/** · Bearer (driver/rider).\n\n"
                                "The new vehicle becomes the active one (status `pending` until staff inspect it). "
                                "Riders must set the helmet flags; `has_passenger_helmet` is required to go online."),
                   request=VehicleSerializer, responses={201: VehicleSerializer, **errors(400, 401, 403)},
                   examples=[OpenApiExample("Bike", request_only=True, value={"make": "Bajaj", "model": "Boxer", "year": 2021, "color": "Red",
                                                                              "plate_number": "EKY 214 QA", "seats": 1, "has_rider_helmet": True,
                                                                              "has_passenger_helmet": True})])
    def post(self, request):
        serializer = VehicleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        vehicle = services.add_vehicle(request.user.provider_profile, serializer.validated_data)
        return Response(VehicleSerializer(vehicle).data, status=status.HTTP_201_CREATED)


class DocumentView(APIView):
    permission_classes = [IsProvider]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(tags=["Provider"], summary="List my documents",
                   description="**GET /api/v1/provider/documents/** · Bearer (driver/rider). Latest first; check `status` and `rejection_reason`.",
                   responses={200: DocumentSerializer(many=True), **errors(401, 403)})
    def get(self, request):
        return Response(DocumentSerializer(request.user.provider_profile.documents.order_by("-created_at"), many=True,
                                           context={"request": request}).data)

    @extend_schema(tags=["Provider"], summary="Upload a document",
                   description=("**POST /api/v1/provider/documents/** · Bearer (driver/rider) · **multipart/form-data**.\n\n"
                                "Fields: `doc_type` (drivers_licence | riders_licence | insurance | vehicle_registration | national_id | profile_photo), "
                                "`file` (front), optional `back_file`, `number`, `expires_at` (YYYY-MM-DD). "
                                "Re-uploading a rejected document replaces it in the review queue."),
                   request={"multipart/form-data": DocumentSerializer}, responses={201: DocumentSerializer, **errors(400, 401, 403)})
    def post(self, request):
        profile = request.user.provider_profile
        serializer = DocumentSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        doc = serializer.save(provider=profile)
        # Older versions of the same document are superseded (kept for audit, hidden from review).
        ProviderDocument.objects.filter(provider=profile, doc_type=doc.doc_type, status="pending").exclude(pk=doc.pk).update(
            status="rejected", rejection_reason="Replaced by a newer upload.")
        services.maybe_submit_for_review(profile)
        return Response(DocumentSerializer(doc, context={"request": request}).data, status=status.HTTP_201_CREATED)


class StatusView(APIView):
    permission_classes = [IsProvider]

    @extend_schema(tags=["Provider"], summary="Go online / offline",
                   description=("**POST /api/v1/provider/status/** · Bearer (driver/rider).\n\n"
                                "Errors: 403 `provider_not_approved`; 409 `no_approved_vehicle`, `helmet_required` (riders), "
                                "`document_expired`, `active_ride` (can't go offline mid-trip). "
                                "After going online, start sending POST /provider/location/ and polling GET /provider/offers/current/."),
                   request=StatusSerializer, responses={200: ProviderProfileSerializer, **errors(400, 401, 403, 409)})
    def post(self, request):
        data = StatusSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        profile = services.set_online(request.user.provider_profile, data.validated_data["is_online"])
        return Response(ProviderProfileSerializer(profile, context={"request": request}).data)


class LocationView(APIView):
    permission_classes = [IsProvider]
    throttle_scope = "location"   # 120/min

    @extend_schema(tags=["Provider"], summary="Send my current location",
                   description=("**POST /api/v1/provider/location/** · Bearer (driver/rider).\n\n"
                                "Send every 4-5 seconds while online (expo-location background task). Used for matching and "
                                "shown to the customer during the trip. Returns 204."),
                   request=LocationSerializer, responses={204: None, **errors(400, 401, 403, 429)})
    def post(self, request):
        data = LocationSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        services.update_location(request.user.provider_profile, data.validated_data["lat"], data.validated_data["lng"],
                                 data.validated_data.get("heading"))
        return Response(status=status.HTTP_204_NO_CONTENT)
